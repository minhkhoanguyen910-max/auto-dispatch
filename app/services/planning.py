"""Automatic route planning for each driver (Plan 1, step 4).

`replan(driver)` works out the order of the driver's stops and the planned arrival at each, and saves them: the
driver's sequence (`list_order_ids`, `route_planned_at`) and each order's stop number, planned arrival range and
whether it's based on estimates. It runs whenever something changes for the driver (an order accepted, edited,
canceled or delivered, a status change) and from the bot's 3-minute check when a driver is ahead of or behind
plan. Nothing happens while ROUTE_OPTIMIZATION is off.

Rules (agreed, see docs/plans/plan-1-improvements.md):
- The stop the driver is heading to ("On my way") or at ("Arrived") stays first; only the stops after it move.
  Exception: if its address changed, it's planned like any other stop, and if another stop comes first, that one
  becomes "On my way" and the old one goes back to "Accepted".
- "On the way": a newly accepted order that lies on the way to the current stop goes first, if the detour and the
  current customer's delay are small (live traffic for the 3 legs involved), the current stop isn't urgent, its
  deadline is kept, and the driver hasn't announced their arrival there. Statuses swap the same way.
- A new order, or a stop whose address changed, is fitted into the best gap of the current sequence without
  moving the other stops; the fully optimized sequence is used only if it saves at least
  REROUTE_THRESHOLD_MINUTES, or keeps a deadline the current one would miss. A removed stop leaves the others in
  their order.
- The route starts from the locked stop; right after a delivery from that stop; otherwise from the driver's GPS.
- A stop is at its agreed meeting point instead of the address (Plan 4, `meeting.stop_geometry`).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ACTIVE_STATUSES, FINAL_STATUSES, Driver, Order, OrderEvent, OrderStatus
from app.services import eta, meeting, optimizer, travel
from app.services.optimizer import Plan, Stop
from app.services.travel import LatLng, straight_line_m

log = logging.getLogger(__name__)

# Right after a delivery, the driver counts as being at that stop if it was this recent (and GPS doesn't say
# they've already moved away)
JUST_DELIVERED = timedelta(minutes=10)
JUST_DELIVERED_NEAR_M = 300
# The bot checks drivers heading to a stop this often, and re-plans when they're this far off plan
DRIFT_CHECK_EVERY = timedelta(minutes=3)
DRIFT_LIMIT = timedelta(minutes=3)


@dataclass
class Swap:
    started: int  # order set to "On my way" automatically
    moved: int  # order that was "On my way", back to "Accepted" (comes after `started`)
    on_the_way: bool  # True: new order on the way; False: the current stop's address changed


@dataclass
class RouteChange:
    """What a re-plan did, for telling the driver (step 5)."""

    sequence: list[int]
    previous: list[int]
    swap: Swap | None = None
    reshuffled: bool = False  # the fully optimized sequence replaced the current one
    late: dict[int, timedelta] = field(default_factory=dict)  # deadlines that will be missed


def utcnow() -> datetime:
    return datetime.now(UTC)


async def _position(session: AsyncSession, driver_id: int) -> LatLng | None:
    loc = func.geometry(Driver.last_location)
    row = (await session.execute(select(func.ST_Y(loc), func.ST_X(loc)).where(Driver.id == driver_id))).one_or_none()
    return (row[0], row[1]) if row and row[0] is not None else None


async def _active(session: AsyncSession, driver_id: int) -> list[tuple[Order, LatLng | None]]:
    loc = meeting.stop_geometry()
    rows = await session.execute(
        select(Order, func.ST_Y(loc), func.ST_X(loc))
        .where(Order.driver_id == driver_id, Order.status.in_(ACTIVE_STATUSES))
        .order_by(Order.id)
        .execution_options(populate_existing=True)
    )
    return [(o, (lat, lng) if lat is not None else None) for o, lat, lng in rows]


def _snapshot(active: list[tuple[Order, LatLng | None]]) -> list[tuple]:
    return [(o.id, o.status, loc) for o, loc in active]


async def _status_time(session: AsyncSession, order_id: int, status: OrderStatus) -> datetime | None:
    return await session.scalar(
        select(func.max(OrderEvent.created_at)).where(OrderEvent.order_id == order_id, OrderEvent.to_status == status)
    )


async def _just_delivered(session: AsyncSession, driver_id: int, now: datetime, gps: LatLng | None) -> LatLng | None:
    """Where the driver's latest delivery was, if it was in the last few minutes and they haven't moved away."""
    loc = meeting.stop_geometry()
    row = (
        await session.execute(
            select(func.ST_Y(loc), func.ST_X(loc))
            .join(OrderEvent, OrderEvent.order_id == Order.id)
            .where(
                Order.driver_id == driver_id,
                OrderEvent.to_status == OrderStatus.COMPLETED,
                OrderEvent.created_at >= now - JUST_DELIVERED,
                Order.dropoff_location.is_not(None),
            )
            .order_by(OrderEvent.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    stop = (row[0], row[1])
    return stop if gps is None or straight_line_m(gps, stop) <= JUST_DELIVERED_NEAR_M else None


def _first_by(previous: list[int], orders: list[Order]) -> Order:
    """Of several orders, the one earliest in the previous sequence (else the oldest)."""
    return min(orders, key=lambda o: (previous.index(o.id) if o.id in previous else len(previous), o.id))


def _late_total(plan: Plan) -> timedelta:
    return sum(plan.late.values(), timedelta(0))


def fewer_late(a: Plan, b: Plan) -> bool:
    """`a` misses deadlines less than `b`: fewer late customers (rule B, LATE_CUSTOMER_PENALTY_MINUTES > 0), else
    fewer minutes late in total (rule A)."""
    if settings.late_customer_penalty_minutes > 0 and len(a.late) != len(b.late):
        return len(a.late) < len(b.late)
    return _late_total(a) < _late_total(b)


async def _current_stop(
    session: AsyncSession, active: list[tuple[Order, LatLng | None]], previous: list[int], gps: LatLng | None, now: datetime,
    moved_order_id: int | None = None,
) -> tuple[Order | None, list[int], dict[int, datetime], bool]:
    """The stop the driver is at or heading to (stays first unless its address just changed): the order, the locked
    ids, its arrival, and whether that arrival is an estimate."""
    at_stop = [o for o, _ in active if o.status == OrderStatus.AT_STOP]
    en_route = [o for o, _ in active if o.status == OrderStatus.EN_ROUTE]
    if at_stop:
        current = _first_by(previous, at_stop)
        return current, [current.id], {current.id: await _status_time(session, current.id, OrderStatus.AT_STOP) or now}, False
    if not en_route:
        return None, [], {}, False
    current = _first_by(previous, en_route)
    if current.id == moved_order_id:
        return current, [], {}, False
    loc = dict((o.id, l) for o, l in active)[current.id]
    if gps and loc:
        leg = (await travel.matrix(session, [gps], [loc], traffic=True))[0][0]
        return current, [current.id], {current.id: now + timedelta(seconds=leg.seconds)}, leg.source != "google"
    return current, [current.id], {current.id: now}, True


async def _route_start(
    session: AsyncSession, driver_id: int, active: list[tuple[Order, LatLng | None]], locked: list[int],
    arrivals: dict[int, datetime], gps: LatLng | None, now: datetime, service: int,
) -> tuple[LatLng | None, datetime, bool]:
    """Where and when the rest of the route starts: after the locked stops; else right after a delivery from that
    stop, or from GPS. Without either, from the first stop with rough times (True = estimate)."""
    if locked:
        last = locked[-1]
        start = dict((o.id, l) for o, l in active)[last]
        return start, max(now, arrivals[last] + timedelta(seconds=service)), False
    start = await _just_delivered(session, driver_id, now, gps) or gps
    if start is None:
        first = next((loc for o, loc in active if o.id not in locked and loc), None)
        return first, now, True
    return start, now, False


@dataclass
class Offer:
    """What taking a new order would cost a driver (route planning's driver choice)."""

    extra: timedelta  # how much later they'd finish
    late: timedelta  # how much later their deadlines would be missed (0: every deadline still kept)
    known: bool  # False: no position to plan from (no GPS, no stops), so the numbers mean nothing
    late_customers: int = 0  # how many more customers would miss their deadline

    @property
    def key(self) -> tuple:
        """Smaller is better: deadlines first (rule B: fewest newly late customers; rule A: any lateness at all), then
        minutes late, then how much later the driver finishes."""
        first = self.late_customers if settings.late_customer_penalty_minutes > 0 else self.late > timedelta(0)
        return (not self.known, first, self.late, self.extra)


async def offer_cost(session: AsyncSession, driver_id: int, order: Order, order_loc: LatLng) -> Offer:
    """How much later the driver would finish, and how much later deadlines would be missed, if the new order were
    fitted into the best gap of their route (the same rules as `replan`)."""
    now = utcnow()
    driver = await session.get(Driver, driver_id)
    active = await _active(session, driver_id)
    previous = list(driver.list_order_ids or [])
    service = await eta.stop_seconds(session, now)
    gps = await _position(session, driver_id)
    _, locked, arrivals, _ = await _current_stop(session, active, previous, gps, now)
    start, start_time, _ = await _route_start(session, driver_id, active, locked, arrivals, gps, now, service)
    if start is None:
        start = gps
    if start is None:
        return Offer(timedelta(0), timedelta(0), known=False)
    remaining = [(o, loc) for o, loc in active if o.id not in locked]
    stops = [Stop(o.id, loc, o.is_urgent, o.deliver_by) for o, loc in remaining]
    new = Stop(order.id, order_loc, order.is_urgent, order.deliver_by)
    points = [s.location for s in [*stops, new]]
    m = await travel.matrix(session, [start, *points], points, nearest=settings.routes_nearest)
    from_start, legs = m[0], m[1:]
    base_seq = [i for i in previous if i in {s.order_id for s in stops}] + [s.order_id for s in stops if s.order_id not in previous]
    n = len(stops)
    base = optimizer.evaluate(start_time, from_start[:n], [row[:n] for row in legs[:n]], stops, service, base_seq)
    with_new = min(
        (optimizer.evaluate(start_time, from_start, legs, [*stops, new], service, base_seq[:k] + [new.order_id] + base_seq[k:]) for k in range(n + 1)),
        key=lambda plan: plan.cost,
    )
    finish_before = base.finish if n else start_time
    late_before = len(base.late) if n else 0
    return Offer(with_new.finish - finish_before, _late_total(with_new) - _late_total(base), known=True,
                 late_customers=len(with_new.late) - late_before)


@dataclass
class _Result:
    snapshot: list[tuple]
    change: RouteChange
    arrivals: dict[int, datetime]
    estimated: bool
    now: datetime


async def _compute(session: AsyncSession, driver_id: int, new_order_id: int | None, moved_order_id: int | None) -> _Result:
    now = utcnow()
    driver = await session.get(Driver, driver_id, populate_existing=True)
    active = await _active(session, driver_id)
    previous = list(driver.list_order_ids or [])
    by_id = {o.id: (o, loc) for o, loc in active}
    kept_previous = [i for i in previous if i in by_id]
    if not active:
        return _Result(_snapshot(active), RouteChange([], previous), {}, False, now)

    service = await eta.stop_seconds(session, now)
    gps = await _position(session, driver_id)
    estimated = False
    arrivals: dict[int, datetime] = {}
    swap: Swap | None = None

    # ---- the stop the driver is at or heading to stays first (unless its address just changed)
    current, locked, first_arrival, estimated = await _current_stop(session, active, previous, gps, now, moved_order_id)
    arrivals.update(first_arrival)

    # ---- "on the way": a new order between the driver and their current stop goes first
    new = by_id.get(new_order_id) if new_order_id else None
    if (
        new
        and current is not None
        and locked == [current.id]
        and current.status == OrderStatus.EN_ROUTE
        and new[0].status == OrderStatus.ACCEPTED
        and settings.on_the_way_max_detour_minutes > 0
        and settings.on_the_way_max_delay_minutes > 0
        and not current.is_urgent
        and current.eta_reported_at is None
        and gps
        and new[1]
        and by_id[current.id][1]
    ):
        n_loc, c_loc = new[1], by_id[current.id][1]
        m = await travel.matrix(session, [gps, n_loc], [n_loc, c_loc], traffic=True)
        to_new, to_current, new_to_current = m[0][0].seconds, m[0][1].seconds, m[1][1].seconds
        detour = to_new + new_to_current - to_current
        current_arrival = now + timedelta(seconds=to_new + service + new_to_current)
        if (
            detour <= settings.on_the_way_max_detour_minutes * 60
            and detour + service <= settings.on_the_way_max_delay_minutes * 60
            and (current.deliver_by is None or current_arrival <= current.deliver_by)
        ):
            swap = Swap(started=new[0].id, moved=current.id, on_the_way=True)
            locked = [new[0].id, current.id]
            arrivals[new[0].id] = now + timedelta(seconds=to_new)
            arrivals[current.id] = current_arrival
            estimated |= any(m[i][j].source != "google" for i, j in ((0, 0), (1, 1)))

    # ---- plan the remaining stops from where the locked ones end (or the driver is)
    start, start_time, rough = await _route_start(session, driver_id, active, locked, arrivals, gps, now, service)
    estimated |= rough
    remaining = [o for o, _ in active if o.id not in locked]

    stops = [Stop(o.id, by_id[o.id][1], o.is_urgent, o.deliver_by) for o in remaining]
    if start is None or not stops:
        plan = optimizer.solve(start_time, [travel.Leg(None, "unknown")] * len(stops), [], stops, service)
        reshuffled = False
    else:
        points = [s.location for s in stops]
        m = await travel.matrix(session, [start, *points], points, nearest=settings.routes_nearest)
        from_start, legs = m[0], m[1:]
        best = optimizer.solve(start_time, from_start, legs, stops, service)
        # A stop whose address changed is placed again like a new order (its old place was for the old address)
        base = [i for i in kept_previous if i not in locked and i != moved_order_id]
        if not base:
            plan, reshuffled = best, False
        else:
            # Fit stops new to the sequence into its best gap, one at a time, without moving the others
            seq = list(base)
            for o in remaining:
                if o.id in seq:
                    continue
                options = [seq[:k] + [o.id] + seq[k:] for k in range(len(seq) + 1)]
                seq = min(options, key=lambda s: optimizer.evaluate(start_time, from_start, legs, stops, service, s).cost)
            kept = optimizer.evaluate(start_time, from_start, legs, stops, service, seq)
            saves = timedelta(seconds=kept.cost - best.cost)
            keeps_deadline = fewer_late(best, kept)
            if saves >= timedelta(minutes=settings.reroute_threshold_minutes) or keeps_deadline:
                plan, reshuffled = best, best.sequence != kept.sequence
            else:
                plan, reshuffled = kept, False
    estimated |= plan.estimated
    arrivals.update(plan.arrivals)
    sequence = locked + plan.sequence

    # ---- the current stop's address changed and another stop now comes first: swap who's "On my way"
    if current is not None and current.id == moved_order_id and sequence and sequence[0] != current.id:
        swap = Swap(started=sequence[0], moved=current.id, on_the_way=False)

    late = {o: arrivals[o] - by_id[o][0].deliver_by for o in sequence if o in arrivals and by_id[o][0].deliver_by and arrivals[o] > by_id[o][0].deliver_by}
    change = RouteChange(sequence, previous, swap, reshuffled, late)
    return _Result(_snapshot(active), change, arrivals, estimated, now)


async def replan(
    session: AsyncSession, driver_id: int, *, new_order_id: int | None = None, moved_order_id: int | None = None
) -> RouteChange | None:
    """Re-plan the driver's route and save it. `new_order_id`: just accepted (for "on the way");
    `moved_order_id`: its address just changed (unlocks it if it's the current stop)."""
    if not settings.route_optimization:
        return None
    for attempt in range(3):
        result = await _compute(session, driver_id, new_order_id, moved_order_id)
        # Driving times are fetched (and committed) above; now lock the driver and check nothing changed meanwhile
        driver = await session.get(Driver, driver_id, with_for_update=True, populate_existing=True)
        active = await _active(session, driver_id)
        if _snapshot(active) == result.snapshot or attempt == 2:
            break
        await session.rollback()
    await _save(session, driver, active, result)
    return result.change


async def _save(session: AsyncSession, driver: Driver, active: list[tuple[Order, LatLng | None]], result: _Result) -> None:
    change, now = result.change, result.now
    orders = {o.id: o for o, _ in active}
    if change.swap:
        started, moved = orders[change.swap.started], orders[change.swap.moved]
        why = "on the way" if change.swap.on_the_way else f"#{moved.shown_number}'s address changed"
        session.add(OrderEvent(order_id=started.id, from_status=started.status, to_status=OrderStatus.EN_ROUTE,
                               note=f"Set automatically ({why}): deliver before #{moved.shown_number}"))
        session.add(OrderEvent(order_id=moved.id, from_status=moved.status, to_status=OrderStatus.ACCEPTED,
                               note=f"Moved after #{started.shown_number} ({why})"))
        started.status = OrderStatus.EN_ROUTE
        moved.status = OrderStatus.ACCEPTED
        # Its "arriving in N min" no longer holds
        moved.eta_reported_at = moved.eta_at = None

    for position, order_id in enumerate(change.sequence, start=1):
        order = orders[order_id]
        order.route_position = position
        arrival = result.arrivals.get(order_id)
        order.planned_arrival_at = arrival
        order.planned_arrival_latest = eta.arrival_range(arrival, now, result.estimated) if arrival else None
        order.eta_estimated = result.estimated
        # A meeting point request still waiting for the customer: its "send by" follows the new arrival (Plan 4)
        m = order.meeting
        if arrival and m and m.status in meeting.OPEN and not m.parked_here:
            m.send_by = arrival - meeting.NOTICE
    driver.list_order_ids = change.sequence
    driver.route_planned_at = now
    # Finished orders no longer have a place in the route
    await session.execute(
        update(Order)
        .where(Order.driver_id == driver.id, Order.status.in_(FINAL_STATUSES), Order.route_position.is_not(None))
        .values(route_position=None, planned_arrival_at=None, planned_arrival_latest=None)
    )
    await session.commit()


async def check_drift(session: AsyncSession) -> list[tuple[int, RouteChange]]:
    """For each driver heading to a stop: compare the live-traffic arrival with the plan; re-plan when they're more
    than DRIFT_LIMIT ahead or behind. Returns the re-plans (driver id, what changed)."""
    if not settings.route_optimization:
        return []
    loc = meeting.stop_geometry()
    rows = (
        await session.execute(
            select(Order.driver_id, Order.planned_arrival_at, func.ST_Y(loc), func.ST_X(loc))
            .where(Order.status == OrderStatus.EN_ROUTE, Order.planned_arrival_at.is_not(None), Order.dropoff_location.is_not(None))
        )
    ).all()
    replanned = []
    for driver_id, planned, lat, lng in rows:
        gps = await _position(session, driver_id)
        if gps is None:
            continue
        leg = (await travel.matrix(session, [gps], [(lat, lng)], traffic=True))[0][0]
        if abs(utcnow() + timedelta(seconds=leg.seconds) - planned) > DRIFT_LIMIT:
            replanned.append((driver_id, await replan(session, driver_id)))
    return replanned
