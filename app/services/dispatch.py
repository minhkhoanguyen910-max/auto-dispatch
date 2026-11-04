"""Order dispatch: offering orders to drivers one at a time, and the order's life cycle.

The bot process runs `dispatch_tick` every few seconds. Each tick:
  1. expires offers nobody answered within OFFER_TIMEOUT_SECONDS;
  2. offers every PENDING order that has no open offer to the best remaining driver.
So a declined or expired offer moves on to the next driver on the following tick.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape

from sqlalchemy import DateTime, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import (
    ACTIVE_STATUSES,
    FINAL_STATUSES,
    TRANSITIONS,
    Customer,
    Driver,
    DriverMessage,
    MeetingRequest,
    MeetingSpot,
    MeetingStatus,
    OfferResult,
    Order,
    OrderChangeRequest,
    OrderEvent,
    OrderItem,
    OrderMessage,
    OrderOffer,
    OrderStatus,
    Product,
)
from app.services import meeting, planning, replies, route
from app.services.eta import format_local, zone
from app.services.notifier import DeliveryError, Notifier
from app.telegram_ui import ASK_ETA_MINUTES, delivery_list, eta_question, is_waiting, minutes_range, money, not_here_question, order_details

log = logging.getLogger(__name__)

# Statuses a driver may set from Telegram; the rest are set by the system or a dispatcher
DRIVER_STATUSES = {OrderStatus.EN_ROUTE, OrderStatus.AT_STOP, OrderStatus.COMPLETED}
# Postgres advisory lock taken while a new order gets the day's next number
ORDER_NUMBER_LOCK = 8_401_101


def utcnow() -> datetime:
    return datetime.now(UTC)


def _event(order: Order, to_status: OrderStatus, *, user_id=None, driver_id=None, note=None) -> OrderEvent:
    return OrderEvent(
        order_id=order.id, from_status=order.status, to_status=to_status, user_id=user_id, driver_id=driver_id, note=note
    )


# ---------------------------------------------------------------- choosing drivers


async def next_candidate(session: AsyncSession, order: Order) -> tuple[Driver, float | None] | None:
    """Best driver not yet given it in this dispatch round, with their distance to the drop-off (m).

    Eligible: active, on duty and connected to Telegram (and the chosen driver only, if one was picked).
    Order of preference: GPS online, then nearest to the drop-off (when both positions are known),
    then fewest active orders.
    """
    already_offered = select(OrderOffer.driver_id).where(
        OrderOffer.order_id == order.id, OrderOffer.sent_at >= order.dispatch_started_at
    )
    dropoff = select(Order.dropoff_location).where(Order.id == order.id).scalar_subquery()
    distance = func.ST_Distance(Driver.last_location, dropoff)
    online = Driver.last_seen_at >= utcnow() - timedelta(minutes=settings.driver_online_minutes)
    active_orders = (
        select(func.count(Order.id))
        .where(Order.driver_id == Driver.id, Order.status.in_(ACTIVE_STATUSES))
        .correlate(Driver)
        .scalar_subquery()
    )
    query = (
        select(Driver, distance)
        .where(
            Driver.is_active,
            Driver.on_duty,
            Driver.telegram_user_id.is_not(None),
            ~Driver.recorded_by_dispatcher,
            Driver.id.not_in(already_offered),
        )
        .order_by(func.coalesce(online, False).desc(), distance.asc().nulls_last(), active_orders, Driver.id)
        .limit(1)
    )
    if order.target_driver_id is not None:
        query = query.where(Driver.id == order.target_driver_id)
    if not settings.route_optimization:
        row = (await session.execute(query)).first()
        return (row[0], row[1]) if row else None
    return await _least_extra_time(session, order, query.limit(None), online)


# Route planning looks at this many of the nearest eligible drivers (each costs a few Google pairs)
CANDIDATES = 5


async def _least_extra_time(session: AsyncSession, order: Order, query, online) -> tuple[Driver, float | None] | None:
    """Route planning's choice: of the eligible drivers (GPS online ones first, as before), the one who'd finish the
    least later with this order in the best gap of their route, while all their deadlines are still kept; if every
    driver would miss one, the one making the fewest customers late (rule B; rule A: missing them least). Distance
    breaks ties."""
    rows = (await session.execute(query.add_columns(func.coalesce(online, False)))).all()
    if not rows:
        return None
    group = [r for r in rows if r[2]] or rows  # GPS online drivers first
    group = group[:CANDIDATES]
    loc = func.geometry(Order.dropoff_location)
    point = (await session.execute(select(func.ST_Y(loc), func.ST_X(loc)).where(Order.id == order.id))).one()
    if point[0] is None:
        return group[0][0], group[0][1]  # no map location: nearest-first can't be improved on
    best, best_key = None, None
    for driver, distance, _ in group:
        cost = await planning.offer_cost(session, driver.id, order, (point[0], point[1]))
        key = (*cost.key, distance if distance is not None else float("inf"))
        if best_key is None or key < best_key:
            best, best_key = (driver, distance), key
    return best


# ---------------------------------------------------------------- the dispatch loop


async def expire_offers(session: AsyncSession, notifier: Notifier) -> int:
    cutoff = utcnow() - timedelta(seconds=settings.offer_timeout_seconds)
    offers = (
        await session.scalars(
            select(OrderOffer)
            .where(OrderOffer.result.is_(None), OrderOffer.sent_at < cutoff)
            .options(selectinload(OrderOffer.order), selectinload(OrderOffer.driver))
            .with_for_update(of=OrderOffer, skip_locked=True)
        )
    ).all()
    for offer in offers:
        offer.result = OfferResult.EXPIRED
        offer.responded_at = utcnow()
    await session.commit()
    for offer in offers:
        await notifier.refresh(offer.driver, offer.order, offer)
    return len(offers)


async def give_pending_orders(session: AsyncSession, notifier: Notifier) -> int:
    """Orders are given, not offered (Plan 5): each order waiting for a driver goes straight to the chosen driver, or
    (Automatic) the best driver on duty, with no Accept / Decline. With nobody on duty it waits, and goes to the first
    driver who comes on duty."""
    orders = (await session.scalars(select(Order).where(Order.status == OrderStatus.PENDING).order_by(Order.id))).all()
    given = 0
    for order in orders:
        candidate = await next_candidate(session, order)
        if candidate is None:
            continue  # nobody on duty (or the chosen driver isn't); the web page shows "no driver"
        driver, distance = candidate
        order = await session.get(Order, order.id, with_for_update=True, populate_existing=True)
        if order.status != OrderStatus.PENDING:
            await session.commit()
            continue  # canceled or given meanwhile
        # The offer row is the driver's card (its Telegram message), accepted from the start
        offer = OrderOffer(order_id=order.id, driver_id=driver.id, result=OfferResult.ACCEPTED, responded_at=utcnow())
        session.add(offer)
        how = "chosen by the dispatcher" if order.target_driver_id else "automatic"
        session.add(_event(order, OrderStatus.ACCEPTED, driver_id=driver.id, note=f"Given to {driver.name} ({how})"))
        order.status = OrderStatus.ACCEPTED
        order.driver_id = driver.id
        await session.commit()
        try:
            offer.telegram_message_id = await notifier.send_offer(driver, order, offer, distance)
        except DeliveryError as err:
            # Can't reach them (e.g. they blocked the bot): take it back; the next round tries the next driver
            log.warning("Can't reach driver %s for order %s: %s", driver.id, order.id, err)
            offer.result = OfferResult.EXPIRED
            session.add(_event(order, OrderStatus.PENDING, driver_id=driver.id, note=f"{driver.name} can't be reached in Telegram"))
            order.status = OrderStatus.PENDING
            order.driver_id = None
            await session.commit()
            continue
        await session.commit()
        given += 1
        change = await planning.replan(session, driver.id, new_order_id=order.id)
        await route_changed(session, driver.id, change, notifier)
        await notifier.refresh(driver, order, offer)
        await send_delivery_list(session, driver.id, notifier)
    return given


async def expire_meetings(session: AsyncSession, notifier: Notifier) -> int:
    """Meeting point requests nobody answered by the time the driver gets to the spot: deliver to the door. ("I'm
    parked here" requests have no deadline; the driver's 5-minute wait handles those.)"""
    now = utcnow()
    requests = (
        await session.scalars(
            select(MeetingRequest)
            .join(Order, Order.id == MeetingRequest.order_id)
            .where(
                MeetingRequest.status.in_(meeting.OPEN),
                MeetingRequest.send_by.is_not(None),
                MeetingRequest.send_by + meeting.NOTICE < now,
                Order.status.in_(ACTIVE_STATUSES),
            )
            .with_for_update(of=MeetingRequest, skip_locked=True)
        )
    ).all()
    for m in requests:
        m.status = MeetingStatus.EXPIRED
        m.resolved_at = now
        order = await session.get(Order, m.order_id)
        session.add(_event(order, order.status, note="Meeting point request expired (no answer from the customer in time): deliver to the door"))
    await session.commit()
    for m in requests:
        await _tell_driver_meeting(session, m.order_id, notifier)
    return len(requests)


async def ask_customer_not_here(session: AsyncSession, notifier: Notifier) -> int:
    """At an agreed meeting point, CUSTOMER_WAIT after Arrived and not delivered yet: ask the driver "Customer not
    here?" with a button to go to the door. "I'm parked here": CUSTOMER_WAIT after the customer said they're coming,
    or after the request when the dispatcher hasn't answered (the customer may still come; if not, it's recorded).
    Once (again after "Customer is coming")."""
    now = utcnow()
    # When the driver tapped Arrived: the status change itself, not notes added while there (also stored as AT_STOP)
    arrived = (
        select(func.max(OrderEvent.created_at))
        .where(
            OrderEvent.order_id == Order.id,
            OrderEvent.to_status == OrderStatus.AT_STOP,
            OrderEvent.from_status.is_distinct_from(OrderStatus.AT_STOP),
        )
        .correlate(Order)
        .scalar_subquery()
    )
    # A meeting point: from Arrived. "I'm parked here" (already arrived): from when the customer said they're coming,
    # or from the request while nobody has answered
    waiting_since = case(
        (MeetingRequest.status != MeetingStatus.AGREED, MeetingRequest.created_at),
        (MeetingRequest.parked_here, func.greatest(arrived, MeetingRequest.resolved_at)),
        else_=arrived,
    ).cast(DateTime(timezone=True))
    parked_unanswered = and_(MeetingRequest.parked_here, MeetingRequest.status.in_(meeting.OPEN))
    rows = (
        await session.execute(
            select(MeetingRequest, Order)
            .join(Order, Order.id == MeetingRequest.order_id)
            .where(
                or_(MeetingRequest.status == MeetingStatus.AGREED, parked_unanswered),
                MeetingRequest.not_here_asked_at.is_(None),
                Order.status == OrderStatus.AT_STOP,
                waiting_since + meeting.CUSTOMER_WAIT < now,
            )
            .with_for_update(of=MeetingRequest, skip_locked=True)
        )
    ).all()
    for m, _ in rows:
        m.not_here_asked_at = now
    await session.commit()
    for m, order in rows:
        driver = await session.get(Driver, order.driver_id)
        text, keyboard = not_here_question(order)
        message_id = await notifier.message(driver, text, keyboard)
        if message_id is not None:
            await track_messages(session, order.id, driver.telegram_user_id, [message_id])
    return len(rows)


async def dispatch_tick(session: AsyncSession, notifier: Notifier) -> None:
    await expire_offers(session, notifier)  # offers sent before orders were given (Plan 5)
    await give_pending_orders(session, notifier)
    await expire_meetings(session, notifier)
    await ask_customer_not_here(session, notifier)


# ---------------------------------------------------------------- driver actions (Telegram)


@dataclass
class Reply:
    """Short text shown to the driver as a pop-up after pressing a button."""

    text: str


async def _driver_offer(session: AsyncSession, offer_id: int, telegram_user_id: int) -> OrderOffer | None:
    offer = await session.scalar(
        select(OrderOffer)
        .where(OrderOffer.id == offer_id)
        .options(selectinload(OrderOffer.driver), selectinload(OrderOffer.order))
        .with_for_update(of=OrderOffer)
    )
    if offer is None or offer.driver.telegram_user_id != telegram_user_id:
        return None
    return offer


async def accept_offer(session: AsyncSession, offer_id: int, telegram_user_id: int, notifier: Notifier) -> Reply:
    offer = await _driver_offer(session, offer_id, telegram_user_id)
    if offer is None:
        return Reply("This order isn't yours.")
    order = await session.get(Order, offer.order_id, with_for_update=True, populate_existing=True)
    if offer.result is not None or order.status != OrderStatus.PENDING:
        if offer.result is None:
            offer.result = OfferResult.EXPIRED
            offer.responded_at = utcnow()
            await session.commit()
        await notifier.refresh(offer.driver, order, offer)
        return Reply("Sorry, this order is no longer available.")

    offer.result = OfferResult.ACCEPTED
    offer.responded_at = utcnow()
    session.add(_event(order, OrderStatus.ACCEPTED, driver_id=offer.driver_id))
    order.status = OrderStatus.ACCEPTED
    order.driver_id = offer.driver_id
    await session.commit()
    change = await planning.replan(session, offer.driver_id, new_order_id=order.id)
    await route_changed(session, offer.driver_id, change, notifier)
    await notifier.refresh(offer.driver, order, offer)
    await send_delivery_list(session, offer.driver_id, notifier)
    return Reply(f"Order #{order.shown_number} accepted.")


async def decline_offer(session: AsyncSession, offer_id: int, telegram_user_id: int, notifier: Notifier) -> Reply:
    offer = await _driver_offer(session, offer_id, telegram_user_id)
    if offer is None:
        return Reply("This order isn't yours.")
    if offer.result is None:
        offer.result = OfferResult.DECLINED
        offer.responded_at = utcnow()
        await session.commit()
    await notifier.refresh(offer.driver, offer.order, offer)
    return Reply("Declined.")


async def driver_set_status(
    session: AsyncSession, order_id: int, new_status: OrderStatus, telegram_user_id: int, notifier: Notifier,
    follow_plan: bool = True,
) -> Reply:
    order = await session.scalar(
        select(Order).where(Order.id == order_id).options(selectinload(Order.driver)).with_for_update(of=Order)
    )
    if order is None or order.driver is None or order.driver.telegram_user_id != telegram_user_id:
        return Reply("This order isn't yours.")
    if new_status not in DRIVER_STATUSES or new_status not in TRANSITIONS[order.status]:
        # Stale button (pressed twice, or the order changed meanwhile): just show the current state
        await _refresh_accepted_offer(session, order, notifier)
        return Reply("Already updated.")
    if follow_plan and new_status == OrderStatus.EN_ROUTE and is_waiting(order, order.driver.list_order_ids):
        # Route planning decides the order of stops; an old "On my way" button can't skip ahead
        await _refresh_accepted_offer(session, order, notifier)
        return Reply(f"Please finish #{order.driver.list_order_ids[0]} first.")

    session.add(_event(order, new_status, driver_id=order.driver_id))
    # At the door before the customer answered about a meeting point: that's settled, it's the door
    unanswered = order.meeting if order.meeting and order.meeting.status in meeting.OPEN and not order.meeting.parked_here else None
    order.status = new_status
    if new_status == OrderStatus.AT_STOP and unanswered:
        unanswered.status = MeetingStatus.EXPIRED
        unanswered.resolved_at = utcnow()
        session.add(_event(order, new_status, driver_id=order.driver_id, note="Meeting point request expired: the driver arrived before the customer answered"))
    # Delivered at the meeting point. "I'm parked here" too when nobody tapped "Customer is coming" (the dispatcher
    # just phoned): the driver waited there and the customer came out
    m = order.meeting
    if new_status == OrderStatus.COMPLETED and m and (m.status == MeetingStatus.AGREED or (m.parked_here and m.status in meeting.OPEN)):
        await _delivered_at_spot(session, order)
    await session.commit()
    change = await planning.replan(session, order.driver_id)
    await route_changed(session, order.driver_id, change, notifier)
    await _refresh_accepted_offer(session, order, notifier)
    if new_status == OrderStatus.COMPLETED:
        accepted = await session.scalar(
            select(OrderOffer.id).where(OrderOffer.order_id == order.id, OrderOffer.result == OfferResult.ACCEPTED)
        )
        await _clean_up_chat(session, order.id, notifier, keep_offer_ids={accepted})
        await send_delivery_list(session, order.driver_id, notifier)
    return Reply({OrderStatus.EN_ROUTE: "On your way.", OrderStatus.AT_STOP: "Arrived.", OrderStatus.COMPLETED: "Delivered. Thank you!"}[new_status])


async def _delivered_at_spot(session: AsyncSession, order: Order) -> None:
    """Delivered at the agreed meeting point: the spot worked (offered as "Same place as last time" for this customer
    and destination, see meeting.usual_spot)."""
    m = order.meeting
    m.status = MeetingStatus.DELIVERED
    m.resolved_at = m.resolved_at or utcnow()
    spot = await session.get(MeetingSpot, m.spot_id, with_for_update=True)
    spot.times_worked += 1
    spot.last_used_at = utcnow()


async def _refresh_accepted_offer(session: AsyncSession, order: Order, notifier: Notifier) -> None:
    # The current driver's card (an order given to another driver has an accepted offer for each)
    offer = await session.scalar(
        select(OrderOffer)
        .where(OrderOffer.order_id == order.id, OrderOffer.result == OfferResult.ACCEPTED, OrderOffer.driver_id == order.driver_id)
        .options(selectinload(OrderOffer.driver))
        .order_by(OrderOffer.sent_at.desc())
        .limit(1)
    )
    if offer is not None:
        await notifier.refresh(offer.driver, order, offer)


async def send_delivery_list(session: AsyncSession, driver_id: int, notifier: Notifier, resort: bool = True, quiet: bool = False) -> None:
    """Send the driver a fresh list of all their current deliveries, sorted nearest first (or, with
    ROUTE_OPTIMIZATION on, in the planned sequence), replacing the previous list. Called whenever one
    of their orders is added, canceled or delivered, or its drop-off moved.

    With resort=False (an edit that didn't move any stop), the stops keep the order of the
    previous list. With quiet=True (only the order numbers changed) the list is changed in place, without
    a notification; it's sent anew only if that message is gone."""
    # Lock the driver so two changes at once don't both send a list
    driver = await session.get(Driver, driver_id, with_for_update=True, populate_existing=True)
    if driver is None or driver.telegram_user_id is None:
        return
    dropoff = meeting.stop_geometry()  # the agreed meeting point, else the address
    rows = (
        await session.execute(
            select(Order, func.ST_Y(dropoff), func.ST_X(dropoff))
            .where(Order.driver_id == driver.id, Order.status.in_(ACTIVE_STATUSES))
            .order_by(Order.id)
        )
    ).all()
    start = None
    if driver.last_location is not None:
        here = func.geometry(Driver.last_location)
        start = tuple((await session.execute(select(func.ST_Y(here), func.ST_X(here)).where(Driver.id == driver.id))).one())
    located = [(order, (lat, lng) if lat is not None else None) for order, lat, lng in rows]

    shown = driver.list_order_ids or []
    if settings.route_optimization:
        # Route planning set the sequence (planning.replan); the list shows it as it is
        located.sort(key=lambda stop: (shown.index(stop[0].id) if stop[0].id in shown else len(shown), stop[0].id))
        stops = route.along(start, located)
    elif not resort and {o.id for o, _ in located} == set(shown):
        located.sort(key=lambda stop: shown.index(stop[0].id))
        stops = route.along(start, located)
    else:
        stops = route.nearest_first(start, located)
    text = delivery_list(stops, has_gps=start is not None)
    if not (quiet and driver.list_message_id and await notifier.edit_list(driver, text, driver.list_message_id)):
        driver.list_message_id = await notifier.send_list(driver, text, driver.list_message_id)
    driver.list_order_ids = [o.id for o, _ in stops]
    await session.commit()


async def route_changed(
    session: AsyncSession, driver_id: int, change: "planning.RouteChange | None", notifier: Notifier, send_list: bool = False
) -> None:
    """After a re-plan (route planning on): tell the driver when their next stop changed, and update the cards
    that gain or lose their "On my way" button. Times shifting alone sends nothing. `send_list`: also send a
    fresh delivery list when the order of stops changed (for callers that don't send one anyway)."""
    if change is None or not change.sequence:
        return
    driver = await session.get(Driver, driver_id)
    orders = {
        o.id: o
        for o in await session.scalars(select(Order).where(Order.id.in_(set(change.sequence) | set(change.previous))))
    }
    seq, prev = change.sequence, change.previous
    # The order the driver could expect without any re-ordering: the previous one, minus finished stops
    expected = [i for i in prev if i in seq]
    locked = 0
    while locked < len(seq) and orders[seq[locked]].status in (OrderStatus.EN_ROUTE, OrderStatus.AT_STOP):
        locked += 1
    text, about = None, None
    if change.swap:
        started, moved = change.swap.started, change.swap.moved
        about = started
        text = (
            f"🔄 <b>New stop on your way:</b> deliver <b>#{started}</b> first, then continue to <b>#{moved}</b>."
            if change.swap.on_the_way
            else f"🔄 <b>#{moved}'s address changed:</b> next stop is now <b>#{started}</b>."
        )
    elif expected and locked < len(seq):
        nxt = seq[locked]
        before = [i for i in expected if orders[i].status == OrderStatus.ACCEPTED]
        if before and before[0] != nxt:
            about = nxt
            after = f"after #{seq[locked - 1]}, " if locked else ""
            text = f"🔄 <b>Route updated:</b> {after}next stop is now <b>#{nxt}</b>."
    if text:
        await _message(session, orders[about], driver, text, notifier)

    # Cards: the one that is next now, the one that was next, and any whose status the re-plan changed
    to_refresh = {seq[0]} | ({prev[0]} if prev and prev[0] in orders else set())
    if change.swap:
        to_refresh |= {change.swap.started, change.swap.moved}
    for order_id in to_refresh:
        if orders[order_id].status not in FINAL_STATUSES:
            await _refresh_accepted_offer(session, orders[order_id], notifier)
    if send_list and (seq != expected or change.swap):
        await send_delivery_list(session, driver_id, notifier)


async def check_route_drift(session: AsyncSession, notifier: Notifier) -> None:
    """Bot's 3-minute check (route planning): re-plan drivers who are off plan, telling them if their next stop
    changed."""
    for driver_id, change in await planning.check_drift(session):
        await route_changed(session, driver_id, change, notifier, send_list=True)


async def track_messages(session: AsyncSession, order_id: int, chat_id: int, message_ids: list[int]) -> None:
    """Remember messages about this order so they're deleted once it's finished."""
    session.add_all(OrderMessage(order_id=order_id, chat_id=chat_id, message_id=m) for m in message_ids)
    await session.commit()


async def _message(session: AsyncSession, order: Order, driver: Driver, text: str, notifier: Notifier) -> None:
    """Send the driver a message about this order, to be cleaned up when the order is finished."""
    message_id = await notifier.message(driver, text)
    if message_id is not None:
        await track_messages(session, order.id, driver.telegram_user_id, [message_id])


async def _clean_up_chat(session: AsyncSession, order_id: int, notifier: Notifier, keep_offer_ids: set[int]) -> None:
    """Order finished: delete every Telegram message about it except the offer cards in
    `keep_offer_ids`. That covers update notices, change reports, and offers other drivers
    declined or let expire."""
    to_delete: dict[int, list[int]] = {}
    offers = await session.scalars(
        select(OrderOffer).where(OrderOffer.order_id == order_id, OrderOffer.telegram_message_id.is_not(None)).options(selectinload(OrderOffer.driver))
    )
    for offer in offers:
        if offer.id not in keep_offer_ids and offer.driver.telegram_user_id:
            to_delete.setdefault(offer.driver.telegram_user_id, []).append(offer.telegram_message_id)
    tracked = (await session.scalars(select(OrderMessage).where(OrderMessage.order_id == order_id))).all()
    for m in tracked:
        to_delete.setdefault(m.chat_id, []).append(m.message_id)
        await session.delete(m)
    await session.commit()
    for chat_id, message_ids in to_delete.items():
        await notifier.delete(chat_id, message_ids)


async def _driver_active_order(session: AsyncSession, order_id: int, telegram_user_id: int, lock: bool = False) -> Order | None:
    """The order, if it's this driver's and still being delivered."""
    query = select(Order).where(Order.id == order_id).options(selectinload(Order.driver))
    if lock:
        query = query.with_for_update(of=Order)
    order = await session.scalar(query)
    if order is None or order.driver is None or order.driver.telegram_user_id != telegram_user_id:
        return None
    return order if order.status in ACTIVE_STATUSES else None


async def driver_report_eta(
    session: AsyncSession, order_id: int, minutes: int, telegram_user_id: int, notifier: Notifier
) -> Reply:
    """Driver tapped "arriving in N min"; the web app tells the dispatcher to contact the customer."""
    order = await _driver_active_order(session, order_id, telegram_user_id, lock=True)
    if order is None:
        return Reply("This order isn't yours or is already finished.")
    if order.status != OrderStatus.EN_ROUTE or not 1 <= minutes <= 30:
        await _refresh_accepted_offer(session, order, notifier)
        return Reply("Already updated.")

    now = utcnow()
    order.eta_reported_at = now
    order.eta_at = now + timedelta(minutes=minutes)
    session.add(_event(order, order.status, driver_id=order.driver_id, note=f"Arriving in ~{minutes} min"))
    await session.commit()
    await _refresh_accepted_offer(session, order, notifier)
    return Reply(f"The dispatcher was told you arrive in ~{minutes} min.")


async def ask_driver_eta(session: AsyncSession, order_id: int, user_id: int, notifier: Notifier) -> Order:
    """Dispatcher asks the order's driver in Telegram when they'll reach it. The question is cleaned up with the
    order's other messages when it's finished."""
    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    if order is None:
        raise LookupError
    if order.driver_id is None or order.status not in ACTIVE_STATUSES:
        raise OrderActionError("Only an order a driver is delivering can be asked about")
    driver = await session.get(Driver, order.driver_id)
    if driver.telegram_user_id is None:
        raise OrderActionError(f"{driver.name} hasn't connected Telegram")
    text, keyboard = eta_question(order)
    name = driver.name
    message_id = await notifier.message(driver, text, keyboard)
    if message_id is None:
        # Not recorded as asked, so the web doesn't wait for an answer to a question the driver never got
        await session.rollback()
        raise OrderActionError(f"Couldn't reach {name} in Telegram")
    order.eta_asked_at = utcnow()
    session.add(_event(order, order.status, user_id=user_id, note="Asked the driver for an ETA"))
    session.add(OrderMessage(order_id=order.id, chat_id=driver.telegram_user_id, message_id=message_id))
    await session.commit()
    return order


async def driver_answer_eta(session: AsyncSession, order_id: int, minutes: int, telegram_user_id: int) -> Reply:
    """Driver answered the dispatcher's "when will you reach #52?"; the web app shows it and pops up."""
    order = await _driver_active_order(session, order_id, telegram_user_id, lock=True)
    if order is None:
        return Reply("This order isn't yours or is already finished.")
    if minutes not in ASK_ETA_MINUTES:
        return Reply("Already updated.")
    now = utcnow()
    or_later = minutes == ASK_ETA_MINUTES[-1]
    order.driver_eta_at = now + timedelta(minutes=minutes)
    order.driver_eta_answered_at = now
    order.driver_eta_or_later = or_later
    said = f"{minutes}+ min" if or_later else f"{minutes} min"
    session.add(_event(order, order.status, driver_id=order.driver_id, note=f"Driver's ETA: {said}"))
    await session.commit()
    return Reply(f"Thanks, the dispatcher was told: {said}.")


async def driver_cant_report_change(session: AsyncSession, order_id: int, telegram_user_id: int) -> Reply | None:
    """Why the driver can't report a change to this order right now, or None if they can.
    Changes can only be reported once the driver has arrived at the customer."""
    order = await _driver_active_order(session, order_id, telegram_user_id)
    if order is None:
        return Reply("This order isn't active anymore.")
    if order.status != OrderStatus.AT_STOP:
        return Reply("You can report a change once you've arrived (reply \"here\" to the order's message).")
    return None


async def driver_customer_not_here(session: AsyncSession, order_id: int, telegram_user_id: int, notifier: Notifier) -> Reply:
    """The customer didn't come to the meeting point: the stop goes back to the door. From an agreed spot the driver
    drives there ("On my way" again); from "I'm parked here" they walk, so they stay Arrived."""
    order = await _driver_active_order(session, order_id, telegram_user_id, lock=True)
    if order is None:
        return Reply("This order isn't active anymore.")
    m = order.meeting
    if m is None or not (m.status == MeetingStatus.AGREED or (m.parked_here and m.status in meeting.OPEN)):
        return Reply("Already updated.")
    m.status = MeetingStatus.NOT_THERE
    spot = await session.get(MeetingSpot, m.spot_id, with_for_update=True)
    spot.times_not_there += 1
    now = utcnow()
    m.resolved_at = now
    session.add(_event(order, order.status, driver_id=order.driver_id, note="Customer wasn't at the meeting point: delivering to the door"))
    if not m.parked_here and order.status == OrderStatus.AT_STOP:
        # Arrived at the spot, not the door: on the way again (not an ordinary status change, so outside TRANSITIONS)
        session.add(_event(order, OrderStatus.EN_ROUTE, driver_id=order.driver_id, note="Back on the way, to the door"))
        order.status = OrderStatus.EN_ROUTE
    await session.commit()
    await _refresh_accepted_offer(session, order, notifier)
    if m.parked_here:
        return Reply("OK: take it to the door. The dispatcher can see it.")
    await _stop_moved(session, order, notifier)
    return Reply("OK: drive to the address. The dispatcher can see it.")


# "I'm parked here" uses the phone's GPS position if it's this recent; otherwise the bot asks for the location
PARKED_GPS_FRESH = timedelta(minutes=2)
# A chosen spot this close to a known one is that spot
SAME_SPOT_M = 25


async def driver_cant_ask_meeting(session: AsyncSession, order_id: int, telegram_user_id: int, parked_here: bool) -> Reply | None:
    """Why the driver can't ask to meet this order's customer at a spot, or None if they can (Plan 4, decision 9).
    `parked_here`: after Arrived, at the driver's current location."""
    order = await _driver_active_order(session, order_id, telegram_user_id)
    if order is None:
        return Reply("This order isn't active anymore.")
    if not parked_here and settings.meeting_points != "all":
        return Reply("Meeting the customer away from the door isn't switched on. Deliver to the door.")
    if order.meeting is not None:
        return Reply(f"You already asked about a meeting point for #{order.shown_number}. The customer is asked only once.")
    customer = await session.get(Customer, order.customer_id) if order.customer_id else None
    if customer is not None and customer.always_to_door:
        return Reply("This customer always wants delivery to the door.")
    if order.dropoff_location is None:
        return Reply("This order's address isn't on the map, so a meeting point can't be checked.")
    if parked_here != (order.status == OrderStatus.AT_STOP):
        return Reply("You've arrived: use 📌 I'm parked here." if order.status == OrderStatus.AT_STOP else "Reply \"here\" to the order's message first.")
    return None


async def driver_usual_spot(session: AsyncSession, order_id: int, telegram_user_id: int) -> MeetingSpot | None:
    """The spot that worked before for this order's customer at this destination, for "Same place as last time"."""
    order = await _driver_active_order(session, order_id, telegram_user_id)
    return await meeting.usual_spot(session, order) if order else None


async def driver_parked_position(session: AsyncSession, telegram_user_id: int) -> tuple[float, float] | None:
    """Where the driver is, if their phone's GPS position is fresh enough for "I'm parked here"."""
    driver_id = await session.scalar(select(Driver.id).where(Driver.telegram_user_id == telegram_user_id))
    return await meeting.driver_position(session, driver_id, PARKED_GPS_FRESH) if driver_id else None


async def driver_check_meeting(
    session: AsyncSession, order_id: int, telegram_user_id: int, spot: tuple[float, float], parked_here: bool
) -> "meeting.Check | Reply":
    """Measure the spot the driver sent (walk, saving, notice), for them to confirm. A refusal is a Reply; a spot that
    can't be used raises meeting.SpotProblem, and OSRM not answering roads.RoutingUnavailable."""
    if (refused := await driver_cant_ask_meeting(session, order_id, telegram_user_id, parked_here)) is not None:
        return refused
    order = await _driver_active_order(session, order_id, telegram_user_id)
    driver = await session.get(Driver, order.driver_id)
    return await meeting.check(session, order, driver, spot, parked_here, utcnow())


async def driver_request_meeting(
    session: AsyncSession, order_id: int, telegram_user_id: int, measured: "meeting.Check", notifier: Notifier
) -> Reply:
    """The driver confirmed the spot: save the request for the dispatcher, who asks the customer (step 4)."""
    if (refused := await driver_cant_ask_meeting(session, order_id, telegram_user_id, measured.parked_here)) is not None:
        return refused
    order = await _driver_active_order(session, order_id, telegram_user_id, lock=True)
    now = utcnow()
    point = f"SRID=4326;POINT({measured.lng} {measured.lat})"
    spot = await session.scalar(
        select(MeetingSpot)
        .where(func.ST_DWithin(MeetingSpot.location, func.ST_GeogFromText(point), SAME_SPOT_M))
        .order_by(func.ST_Distance(MeetingSpot.location, func.ST_GeogFromText(point)))
        .limit(1)
    )
    if spot is None:
        spot = MeetingSpot(location=point, name=measured.name or None)
        session.add(spot)
    spot.name = spot.name or measured.name or None
    spot.times_chosen = (spot.times_chosen or 0) + 1
    spot.last_used_at = now
    order.meeting = MeetingRequest(
        driver_id=order.driver_id,
        spot=spot,
        parked_here=measured.parked_here,
        walk_meters=measured.walk_meters,
        walk_seconds=measured.walk_seconds,
        saving_min_seconds=measured.saving_min_seconds,
        saving_max_seconds=measured.saving_max_seconds,
        send_by=None if measured.parked_here or measured.notice_seconds is None
        else now + timedelta(seconds=measured.notice_seconds) - meeting.NOTICE,
    )
    where = spot.name or "a spot on the map"
    if measured.parked_here:
        note = f"Driver is parked at {where} and asks the customer to come out ({measured.walk_meters} m walk)"
    else:
        saving = f", saves about {minutes_range(measured.saving_min_seconds, measured.saving_max_seconds)}" if measured.saving_seconds is not None else ""
        note = f"Driver asks to meet the customer at {where} ({measured.walk_meters} m walk{saving})"
    session.add(_event(order, order.status, driver_id=order.driver_id, note=note))
    await session.commit()
    await _refresh_accepted_offer(session, order, notifier)
    if measured.parked_here:
        return Reply("📌 Sent to the dispatcher, who will call the customer now. If they don't come within 5 minutes, take it to the door.")
    return Reply("📍 Sent to the dispatcher, who will ask the customer. You'll hear back here; until then, the stop is the door.")


@dataclass
class QuantityRow:
    """One line of the driver's "new quantities" dialog."""

    product_id: int
    name: str
    price: Decimal
    quantity: int  # on the order now


async def quantity_rows(session: AsyncSession, order_id: int) -> list[QuantityRow]:
    """The order's items, then every other active product at 0 (the customer may want one)."""
    order = await session.get(Order, order_id, populate_existing=True)
    rows = [QuantityRow(i.product_id, i.product_name, i.unit_price, i.quantity) for i in order.items]
    on_order = {r.product_id for r in rows}
    for p in await session.scalars(select(Product).where(Product.is_active).order_by(Product.name)):
        if p.id not in on_order:
            rows.append(QuantityRow(p.id, p.name, p.price, 0))
    return rows


async def driver_change_quantities(
    session: AsyncSession, order_id: int, telegram_user_id: int, quantities: dict[int, int], notifier: Notifier
) -> tuple[bool, Reply]:
    """Driver at the door sent new quantities: apply them right away, and leave an (applied) change
    report so the web app alerts the dispatcher. Returns (applied, reply)."""
    if (refused := await driver_cant_report_change(session, order_id, telegram_user_id)) is not None:
        return False, refused
    if not any(q > 0 for q in quantities.values()):
        return False, Reply("All quantities are 0: that's a cancellation, not a change. Please call the dispatcher.")

    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    products = {p.id: p for p in await session.scalars(select(Product).where(Product.id.in_(quantities)))}
    on_order = {i.product_id for i in order.items}
    if any(pid not in on_order and (pid not in products or not products[pid].is_active) for pid, q in quantities.items() if q > 0):
        return False, Reply("One of these products can't be ordered anymore. Please call the dispatcher.")
    # Products the dialog didn't show stay as they are
    quantities = {**{i.product_id: i.quantity for i in order.items}, **quantities}
    changes = _describe_changes(order, order.dropoff_address, order.notes, None, None, quantities, products)
    if not changes:
        return False, Reply("Nothing changed.")

    old_total = order.total
    _set_items(order, quantities, products)
    summary = f"{'; '.join(changes)} · total {money(old_total)} → {money(order.total)}"
    session.add(_event(order, order.status, driver_id=order.driver_id, note=f"Quantities changed at the door: {summary}"))
    session.add(OrderChangeRequest(order_id=order.id, driver_id=order.driver_id, text=summary, applied=True))
    await session.commit()
    await _refresh_accepted_offer(session, order, notifier)
    await send_delivery_list(session, order.driver_id, notifier, resort=False)
    return True, Reply(f"✅ <b>Order #{order.shown_number} updated</b>, the dispatcher was told.\n\n{order_details(order)}")


async def driver_report_change(session: AsyncSession, order_id: int, telegram_user_id: int, text: str) -> Reply:
    if (refused := await driver_cant_report_change(session, order_id, telegram_user_id)) is not None:
        return Reply(f"{refused.text} The change wasn't sent.")
    order = await session.get(Order, order_id)
    session.add(OrderChangeRequest(order_id=order.id, driver_id=order.driver_id, text=text.strip()[:2000]))
    await session.commit()
    return Reply(f"Sent to the dispatcher. You'll get a message when order #{order.shown_number} is updated.")


# ---------------------------------------------------------------- drivers' messages (Plan 5)

# Steps an order goes through as the driver replies; "done" on an accepted order passes On the way and Arrived
_STEPS = [OrderStatus.ACCEPTED, OrderStatus.EN_ROUTE, OrderStatus.AT_STOP, OrderStatus.COMPLETED]


@dataclass
class Answer:
    """How the bot answers a driver's message. Understood: just a reaction on it (👍, or ✍ for a message passed to the
    dispatcher), no extra message. Something wrong ("Which order?", already delivered, ...) or on / off duty: a
    one-line message. `order_id`: the order it's about, still being delivered (the message is tidied away with the
    order's others); `tie_to`: orders it's tied to instead (for "Which order?", until the first is finished);
    `delete`: the message finished its order (done), so it goes right away with the order's other messages."""

    text: str | None = None
    reaction: str | None = None
    order_id: int | None = None
    tie_to: list[int] | None = None
    delete: bool = False


async def _order_of_message(session: AsyncSession, driver: Driver, chat_id: int, message_id: int) -> int | None:
    """The order a message in the driver's chat is about: its card, or another message about it (the bot's notices
    and confirmations, the driver's own replies), so a reply to a reply still finds the order."""
    order_id = await session.scalar(
        select(OrderOffer.order_id).where(OrderOffer.driver_id == driver.id, OrderOffer.telegram_message_id == message_id).limit(1)
    )
    if order_id is None:
        # A message tied to several orders ("Which order?") is about none of them in particular
        tied = (
            await session.scalars(
                select(OrderMessage.order_id).where(OrderMessage.chat_id == chat_id, OrderMessage.message_id == message_id).distinct()
            )
        ).all()
        order_id = tied[0] if len(tied) == 1 else None
    return order_id


async def driver_message(
    session: AsyncSession, telegram_user_id: int, chat_id: int, message_id: int, text: str, reply_to: int | None, notifier: Notifier
) -> Answer:
    """A driver wrote to the bot (not during one of its questions). A reply to an order's message is about that order:
    minutes ("10": on the way, arriving in 10 min), "here" (Arrived), "done" (Delivered), or anything else (to the
    dispatcher). Not a reply: "start" / "finish" (on / off duty); minutes, "here" or "done" count for the driver's
    one order on the go (with several, the bot asks which). Every message is kept and alerts the dispatcher."""
    driver = await session.scalar(select(Driver).where(Driver.telegram_user_id == telegram_user_id))
    if driver is None:
        return Answer("This bot is for drivers. Open the link your dispatcher sent you to connect.")
    meaning = replies.understand(text, await replies.load_words(session))
    order_id = await _order_of_message(session, driver, chat_id, reply_to) if reply_to else None
    if order_id is None and meaning.kind in ("start", "finish"):
        driver.on_duty = meaning.kind == "start"
        result = "on duty" if driver.on_duty else "off duty"
        session.add(DriverMessage(driver_id=driver.id, text=text, meaning=meaning.kind, result=result, telegram_message_id=message_id))
        await session.commit()
        if driver.on_duty:
            return Answer("🟢 You're on duty: new orders will come here. Send \"finish\" when you stop.")
        return Answer("⚪ You're off duty: no new orders. The ones you have stay yours. Send \"start\" to begin again.")
    if order_id is None and meaning.kind in ("minutes", "arrived", "delivered"):
        on_the_go = (
            await session.scalars(select(Order.id).where(Order.driver_id == driver.id, Order.status.in_(ACTIVE_STATUSES)))
        ).all()
        if len(on_the_go) != 1:
            result = "Which order? Reply to its message." if on_the_go else "You have no order on the go."
            session.add(DriverMessage(driver_id=driver.id, text=text, meaning=meaning.kind, result=result, telegram_message_id=message_id))
            await session.commit()
            return Answer(result, tie_to=list(on_the_go))
        order_id = on_the_go[0]
    if order_id is None:
        session.add(DriverMessage(driver_id=driver.id, text=text, meaning="other", result="sent to the dispatcher", telegram_message_id=message_id))
        await session.commit()
        return Answer(reaction="✍")

    if meaning.kind == "minutes":
        answer = await _reply_minutes(session, order_id, meaning.minutes, telegram_user_id, notifier)
    elif meaning.kind == "arrived":
        answer = await _reply_step(session, order_id, OrderStatus.AT_STOP, telegram_user_id, notifier)
    elif meaning.kind == "delivered":
        answer = await _reply_step(session, order_id, OrderStatus.COMPLETED, telegram_user_id, notifier)
    else:
        answer = await _reply_note(session, order_id, text, telegram_user_id)
    session.add(
        DriverMessage(
            driver_id=driver.id, order_id=order_id, text=text, meaning=meaning.kind if meaning.kind in ("minutes", "arrived", "delivered") else "other",
            result=answer.result, telegram_message_id=message_id,
        )
    )
    await session.commit()
    order = await session.get(Order, order_id, populate_existing=True)
    still_open = order_id if order.status in ACTIVE_STATUSES else None
    if not answer.ok:
        return Answer(answer.text, order_id=still_open)
    if meaning.kind == "delivered":
        return Answer(delete=True)
    return Answer(reaction="👍" if meaning.kind in ("minutes", "arrived") else "✍", order_id=still_open)


@dataclass
class _Done:
    text: str  # for the driver, when it didn't work
    result: str  # for the dispatcher's alert: what happened, or why not
    ok: bool = True


async def _not_theirs(session: AsyncSession, order_id: int) -> _Done:
    order = await session.get(Order, order_id)
    if order is not None and order.status == OrderStatus.CANCELED:
        return _Done(f"❌ #{order.shown_number} was canceled. Your message went to the dispatcher.", "the order was canceled", ok=False)
    if order is not None and order.status == OrderStatus.COMPLETED:
        return _Done(f"#{order.shown_number} is already delivered. Your message went to the dispatcher.", "already delivered", ok=False)
    return _Done("This order isn't yours. Your message went to the dispatcher.", "not their order", ok=False)


async def _reply_minutes(session: AsyncSession, order_id: int, minutes: int, telegram_user_id: int, notifier: Notifier) -> _Done:
    """ "10": the driver is on the way and arrives in about 10 minutes."""
    order = await _driver_active_order(session, order_id, telegram_user_id)
    if order is None:
        return await _not_theirs(session, order_id)
    if order.status == OrderStatus.AT_STOP:
        return _Done(f"#{order.shown_number}: you've already arrived. Reply \"done\" when it's delivered.", "already arrived", ok=False)
    if order.status == OrderStatus.ACCEPTED:
        await driver_set_status(session, order_id, OrderStatus.EN_ROUTE, telegram_user_id, notifier, follow_plan=False)
    order = await _driver_active_order(session, order_id, telegram_user_id, lock=True)
    now = utcnow()
    order.eta_reported_at = now
    order.eta_at = now + timedelta(minutes=minutes)
    if order.eta_asked_at and not (order.driver_eta_answered_at and order.driver_eta_answered_at > order.eta_asked_at):
        # Also the answer to the dispatcher's "Ask driver for ETA"
        order.driver_eta_at, order.driver_eta_answered_at, order.driver_eta_or_later = order.eta_at, now, False
    session.add(_event(order, order.status, driver_id=order.driver_id, note=f"Arriving in ~{minutes} min"))
    await session.commit()
    await _refresh_accepted_offer(session, order, notifier)
    when = format_local(order.eta_at)
    return _Done(f"✅ #{order.shown_number}: arrives ~{when}, the dispatcher was told.", f"arrives in ~{minutes} min (~{when})")


async def _reply_step(session: AsyncSession, order_id: int, target: OrderStatus, telegram_user_id: int, notifier: Notifier) -> _Done:
    """ "here" (Arrived) or "done" (Delivered), through the steps before it the driver didn't report."""
    order = await _driver_active_order(session, order_id, telegram_user_id)
    if order is None:
        return await _not_theirs(session, order_id)
    number = order.shown_number
    if _STEPS.index(order.status) >= _STEPS.index(target):
        return _Done(f"#{number}: already arrived. Reply \"done\" when it's delivered.", "already arrived", ok=False)
    for step in _STEPS[_STEPS.index(order.status) + 1 : _STEPS.index(target) + 1]:
        await driver_set_status(session, order_id, step, telegram_user_id, notifier, follow_plan=False)
    if target == OrderStatus.AT_STOP:
        return _Done(f"📍 #{number}: arrived.", "arrived")
    return _Done(f"🎉 #{number} delivered. Thank you!", "delivered")


async def _reply_note(session: AsyncSession, order_id: int, text: str, telegram_user_id: int) -> _Done:
    """Anything else about an order goes to the dispatcher, shown on the order like a change report."""
    order = await _driver_active_order(session, order_id, telegram_user_id)
    if order is None:
        return await _not_theirs(session, order_id)
    session.add(OrderChangeRequest(order_id=order.id, driver_id=order.driver_id, text=text.strip()[:2000]))
    await session.commit()
    return _Done(f"💬 Sent to the dispatcher (#{order.shown_number}).", "sent to the dispatcher")


# ---------------------------------------------------------------- dispatcher actions (web)


class OrderActionError(Exception):
    pass


async def create_order(session: AsyncSession, order: Order, user_id: int) -> Order:
    order.status = OrderStatus.PENDING
    # The day's next number; the lock (held until the commit) keeps two orders created at once from sharing one
    await session.execute(select(func.pg_advisory_xact_lock(ORDER_NUMBER_LOCK)))
    order.order_day = datetime.now(zone()).date()
    order.day_number = (await session.scalar(select(func.max(Order.day_number)).where(Order.order_day == order.order_day)) or 0) + 1
    order.shown_number = await session.scalar(
        select(func.count()).where(Order.order_day == order.order_day, Order.status != OrderStatus.CANCELED)
    ) + 1
    session.add(order)
    await session.flush()
    session.add(OrderEvent(order_id=order.id, from_status=None, to_status=OrderStatus.PENDING, user_id=user_id))
    await session.commit()
    return order


async def cancel_order(session: AsyncSession, order_id: int, user_id: int, reason: str | None, notifier: Notifier) -> Order:
    # The numbers lock first, before any order row: two cancels at once then can't wait on each other's orders
    await session.execute(select(func.pg_advisory_xact_lock(ORDER_NUMBER_LOCK)))
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        raise LookupError
    if order.status in FINAL_STATUSES:
        raise OrderActionError(f"Order is already {order.status.value.lower()}")

    had_driver = order.driver_id is not None
    session.add(_event(order, OrderStatus.CANCELED, user_id=user_id, note=reason))
    order.status = OrderStatus.CANCELED
    renumbered = await _close_number_gap(session, order)
    open_offers = (
        await session.scalars(
            select(OrderOffer).where(OrderOffer.order_id == order.id, OrderOffer.result.is_(None)).options(selectinload(OrderOffer.driver))
        )
    ).all()
    for offer in open_offers:
        offer.result = OfferResult.EXPIRED
        offer.responded_at = utcnow()
    await session.commit()

    for offer in open_offers:
        await notifier.refresh(offer.driver, order, offer)
    if had_driver:
        # The cancel notice (it notifies the driver, an edit wouldn't) becomes the one message left,
        # so it repeats the order's details and the card goes too
        await _clean_up_chat(session, order.id, notifier, keep_offer_ids=set())
        driver = await session.get(Driver, order.driver_id)
        reason_text = f"\nReason: {reason}" if reason else ""
        await notifier.message(
            driver, f"❌ <b>Order #{order.shown_number} was canceled</b> by the dispatcher.{reason_text}\n\n{order_details(order)}"
        )
        change = await planning.replan(session, driver.id)
        await route_changed(session, driver.id, change, notifier)
        await send_delivery_list(session, driver.id, notifier)
    else:
        # Whoever was deciding keeps their card, now saying "canceled"
        await _clean_up_chat(session, order.id, notifier, keep_offer_ids={o.id for o in open_offers})
    await _show_new_numbers(session, renumbered, notifier, skip_list=order.driver_id)
    return order


async def _close_number_gap(session: AsyncSession, canceled: Order) -> list[Order]:
    """The day's later orders move down one shown number ("#3" becomes "#2"), so the numbers have no gap. The
    caller holds ORDER_NUMBER_LOCK."""
    later = (
        await session.scalars(
            select(Order)
            .where(Order.order_day == canceled.order_day, Order.status != OrderStatus.CANCELED, Order.shown_number > canceled.shown_number)
            .with_for_update()
        )
    ).all()
    for o in later:
        o.shown_number -= 1
    return list(later)


async def _show_new_numbers(session: AsyncSession, orders: list[Order], notifier: Notifier, skip_list: int | None) -> None:
    """Drivers' messages about renumbered orders show the new number, changed in place (no notification): the
    order card, an offer still open, and the delivery list (`skip_list`: a driver whose list was just sent)."""
    drivers = set()
    for order in orders:
        if order.status in FINAL_STATUSES:
            continue
        if order.driver_id is not None:
            await _refresh_accepted_offer(session, order, notifier)
            drivers.add(order.driver_id)
        open_offers = await session.scalars(
            select(OrderOffer).where(OrderOffer.order_id == order.id, OrderOffer.result.is_(None)).options(selectinload(OrderOffer.driver))
        )
        for offer in open_offers:
            await notifier.refresh(offer.driver, order, offer)
    for driver_id in drivers - {skip_list}:
        await send_delivery_list(session, driver_id, notifier, resort=False, quiet=True)


async def redispatch(session: AsyncSession, order_id: int, user_id: int) -> Order:
    """Start a new dispatch round: every eligible driver can be asked again."""
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        raise LookupError
    if order.status != OrderStatus.PENDING:
        raise OrderActionError("Only orders waiting for a driver can be sent again")
    open_offer = await session.scalar(
        select(OrderOffer.id).where(and_(OrderOffer.order_id == order.id, OrderOffer.result.is_(None)))
    )
    if open_offer is not None:
        raise OrderActionError("A driver is still deciding on this order")
    # Database clock, the same clock offer times (sent_at) come from
    order.dispatch_started_at = func.now()
    session.add(OrderEvent(order_id=order.id, from_status=OrderStatus.PENDING, to_status=OrderStatus.PENDING, user_id=user_id, note="Sent to drivers again"))
    await session.commit()
    return order


async def update_order(
    session: AsyncSession,
    order_id: int,
    user_id: int,
    *,
    customer: Customer | None,
    dropoff_address: str,
    is_urgent: bool = False,
    deliver_by: datetime | None = None,
    deliver_by_auto: bool = False,
    dropoff_latlng: tuple[float, float] | None,
    notes: str | None,
    quantities: dict[int, int],
    products: dict[int, Product],
    notifier: Notifier,
) -> Order:
    """Dispatcher edits an order, usually after a driver reported a change. Open change reports
    count as handled, and the driver gets the new details in Telegram.

    A delivered order can still have its items (and customer) corrected, e.g. the driver tapped
    Delivered instead of reporting a change; that's recorded in the history and doesn't message
    the driver. Drivers never see the customer, so changing only that doesn't message them either."""
    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    if order is None:
        raise LookupError
    if order.status == OrderStatus.CANCELED:
        raise OrderActionError("Order is already canceled")
    correcting = order.status == OrderStatus.COMPLETED

    old_latlng = None
    if order.dropoff_location is not None:
        loc = func.geometry(Order.dropoff_location)
        old_latlng = tuple((await session.execute(select(func.ST_Y(loc), func.ST_X(loc)).where(Order.id == order.id))).one())
    changes = _describe_changes(order, dropoff_address, notes, old_latlng, dropoff_latlng, quantities, products)
    moved = dropoff_address != order.dropoff_address or not _same_place(old_latlng, dropoff_latlng)
    if correcting and (moved or notes != order.notes):
        raise OrderActionError("Only the items can be corrected on a delivered order")
    if correcting and not quantities:
        raise OrderActionError("A delivered order needs at least one item")

    customer_id = customer.id if customer else None
    if customer_id != order.customer_id:
        previous = await session.get(Customer, order.customer_id) if order.customer_id else None
        history = [*changes, f"customer {previous.name if previous else 'none'} → {customer.name if customer else 'none'}"]
    else:
        history = changes
    urgency_changed = is_urgent != order.is_urgent or deliver_by != order.deliver_by
    # Shown to drivers only once route planning uses them (Plan 1 step 5); for now just recorded
    if is_urgent != order.is_urgent:
        history = [*history, "marked urgent" if is_urgent else "no longer urgent"]
    if deliver_by != order.deliver_by:
        history = [*history, f"deliver by {format_local(deliver_by)}" if deliver_by else "deliver-by time removed"]

    _set_items(order, quantities, products)
    order.is_urgent = is_urgent
    order.deliver_by = deliver_by
    order.deliver_by_auto = deliver_by_auto
    order.customer_id = customer_id
    order.dropoff_address = dropoff_address
    order.dropoff_location = f"SRID=4326;POINT({dropoff_latlng[1]} {dropoff_latlng[0]})" if dropoff_latlng else None
    order.notes = notes
    handled = (
        await session.scalars(
            select(OrderChangeRequest)
            .where(OrderChangeRequest.order_id == order.id, OrderChangeRequest.resolved_at.is_(None))
            .options(selectinload(OrderChangeRequest.driver))
        )
    ).all()
    for request in handled:
        request.resolved_at = utcnow()
        request.resolved_by_id = user_id
    note = f"{'Corrected after delivery' if correcting else 'Order updated'}: {'; '.join(history)}" if history else "Order saved without changes"
    session.add(_event(order, order.status, user_id=user_id, note=note))
    await session.commit()

    if correcting:
        return order  # the driver's chat about this order is already cleaned up
    if order.driver_id is not None and (moved or urgency_changed):
        change = await planning.replan(session, order.driver_id, moved_order_id=order.id if moved else None)
        await route_changed(session, order.driver_id, change, notifier, send_list=not changes)
    if not changes:
        # Nothing to send, except telling a driver who reported a change that it was dealt with
        for driver in {r.driver for r in handled if not r.applied}:
            await _message(session, order, driver, f"✅ The dispatcher handled your change for order #{order.shown_number}.", notifier)
        return order
    if order.driver_id is not None:
        await _refresh_accepted_offer(session, order, notifier)
        driver = await session.get(Driver, order.driver_id)
        await _message(session, order, driver, f"✏️ <b>Order #{order.shown_number} was updated</b> by the dispatcher.\n\n{order_details(order)}", notifier)
        # Only a new drop-off changes the route; otherwise just bring the list's details up to date
        await send_delivery_list(session, driver.id, notifier, resort=moved)
    else:
        # Still being offered: show the driver who is deciding the new details
        for offer in await session.scalars(
            select(OrderOffer).where(OrderOffer.order_id == order.id, OrderOffer.result.is_(None)).options(selectinload(OrderOffer.driver))
        ):
            await notifier.refresh(offer.driver, order, offer)
    return order


def _set_items(order: Order, quantities: dict[int, int], products: dict[int, Product]) -> None:
    """Replace the order's items (product id -> quantity; 0 or missing = not on the order).
    Lines already on the order keep their price; new ones get today's price."""
    old = {i.product_id: i for i in order.items}
    order.items = [
        OrderItem(
            product_id=pid,
            product_name=old[pid].product_name if pid in old else products[pid].name,
            unit_price=old[pid].unit_price if pid in old else products[pid].price,
            quantity=qty,
        )
        for pid, qty in quantities.items()
        if qty > 0
    ]


def _same_place(a: tuple[float, float] | None, b: tuple[float, float] | None) -> bool:
    rounded = [ll and (round(ll[0], 6), round(ll[1], 6)) for ll in (a, b)]
    return rounded[0] == rounded[1]


def _describe_changes(
    order: Order,
    address: str,
    notes: str | None,
    old_latlng: tuple[float, float] | None,
    new_latlng: tuple[float, float] | None,
    quantities: dict[int, int],
    products: dict[int, Product],
) -> list[str]:
    """What an edit changes, for the order history: "Water 2 → 3", "Ice added (1)", ..."""
    changes = []
    old = {i.product_id: i for i in order.items}
    for pid in [*old, *(p for p in quantities if p not in old)]:
        name = old[pid].product_name if pid in old else products[pid].name
        before, after = (old[pid].quantity if pid in old else 0), quantities.get(pid, 0)
        if before == after:
            continue
        changes.append(f"{name} added ({after})" if not before else f"{name} removed" if not after else f"{name} {before} → {after}")
    if address != order.dropoff_address:
        changes.append(f"address “{order.dropoff_address}” → “{address}”")
    if notes != order.notes:
        changes.append(f"notes “{notes}”" if notes else "notes removed")
    if not _same_place(old_latlng, new_latlng):
        changes.append("map location changed" if new_latlng else "map location removed")
    return changes


async def resolve_change_request(session: AsyncSession, order_id: int, request_id: int, user_id: int, notifier: Notifier) -> None:
    """Dispatcher dealt with a driver's change report without editing the order (e.g. by phone)."""
    request = await session.scalar(
        select(OrderChangeRequest)
        .where(OrderChangeRequest.id == request_id, OrderChangeRequest.order_id == order_id)
        .options(selectinload(OrderChangeRequest.driver))
        .with_for_update(of=OrderChangeRequest)
    )
    if request is None:
        raise LookupError
    if request.resolved_at is not None:
        return
    request.resolved_at = utcnow()
    request.resolved_by_id = user_id
    await session.commit()
    if request.applied:
        return  # the dispatcher only acknowledged a change that's already on the order
    order = await session.get(Order, order_id)
    await _message(session, order, request.driver, f"✅ The dispatcher handled your change for order #{order.shown_number}.", notifier)


# ---------------------------------------------------------------- meeting points: the dispatcher asks the customer


async def _open_meeting(session: AsyncSession, order_id: int) -> tuple[Order, MeetingRequest]:
    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    if order is None:
        raise LookupError
    m = order.meeting
    if m is None or m.status not in meeting.OPEN or order.status not in ACTIVE_STATUSES:
        raise OrderActionError("No meeting point request is waiting on this order")
    return order, m


async def meeting_asked(session: AsyncSession, order_id: int, user_id: int, notifier: Notifier) -> Order:
    """The dispatcher copied the message for the customer: now waiting for the customer's answer."""
    order, m = await _open_meeting(session, order_id)
    if m.status == MeetingStatus.REQUESTED:
        m.status = MeetingStatus.ASKED
        m.asked_at = utcnow()
        session.add(_event(order, order.status, user_id=user_id, note=f"Asked the customer to meet at {m.spot.name or 'the spot'}"))
        await session.commit()
        await _refresh_accepted_offer(session, order, notifier)
    return order


async def meeting_answered(session: AsyncSession, order_id: int, user_id: int, agreed: bool, notifier: Notifier) -> Order:
    """The customer agreed or declined (the dispatcher tells the app). A customer who declines DECLINES_FOR_DOOR
    times always gets delivery to the door from then on."""
    order, m = await _open_meeting(session, order_id)
    now = utcnow()
    m.status = MeetingStatus.AGREED if agreed else MeetingStatus.DECLINED
    m.asked_at = m.asked_at or now
    m.resolved_at = now
    m.resolved_by_id = user_id
    where = m.spot.name or "the spot"
    if m.parked_here:
        note = "Customer is coming out to the driver" if agreed else "Customer declined to come out: deliver to the door"
    else:
        note = f"Customer agreed to meet at {where}" if agreed else "Customer declined the meeting point: deliver to the door"
    customer = await session.get(Customer, order.customer_id, with_for_update=True) if order.customer_id else None
    if agreed and m.parked_here:
        m.not_here_asked_at = None  # wait CUSTOMER_WAIT for them from now, even if the bot already asked
    if not agreed and customer is not None:
        customer.meeting_declines += 1
        if customer.meeting_declines >= meeting.DECLINES_FOR_DOOR and not customer.always_to_door:
            customer.always_to_door = True
            note += f"; declined {customer.meeting_declines} times, so from now on always to the door"
    session.add(_event(order, order.status, user_id=user_id, note=note))
    await session.commit()
    await _tell_driver_meeting(session, order.id, notifier)
    if agreed and not m.parked_here:
        await _stop_moved(session, order, notifier)
    return order


async def _stop_moved(session: AsyncSession, order: Order, notifier: Notifier) -> None:
    """The stop is now somewhere else (the meeting point, or back to the door): re-plan the route the same way as
    an address change, and send the driver their list with the stop where it is now."""
    change = await planning.replan(session, order.driver_id, moved_order_id=order.id)
    await route_changed(session, order.driver_id, change, notifier)
    await send_delivery_list(session, order.driver_id, notifier)


async def _tell_driver_meeting(session: AsyncSession, order_id: int, notifier: Notifier) -> None:
    """Tell the driver how their meeting point request ended (a message, so it notifies), and update their card."""
    order = await session.get(Order, order_id, populate_existing=True)
    m, driver = order.meeting, await session.get(Driver, order.driver_id)
    where = escape(m.spot.name) if m.spot.name else "the spot"
    if m.status == MeetingStatus.AGREED:
        if m.parked_here:
            text = f"✅ <b>#{order.shown_number}: the customer is coming out</b> to you. If they're not there within {meeting.PARKED_WAIT_MINUTES} min, take it to the door."
        else:
            when = f" at about {format_local(order.planned_arrival_at)}" if settings.route_optimization and order.planned_arrival_at else ""
            text = f"✅ <b>#{order.shown_number}: the customer will meet you at {where}</b>{when}."
    elif m.status == MeetingStatus.DECLINED:
        text = f"🚪 <b>#{order.shown_number}: the customer prefers the door.</b> Deliver to the door as usual."
    elif m.status == MeetingStatus.EXPIRED:
        text = f"🚪 <b>#{order.shown_number}: no answer from the customer in time.</b> Deliver to the door as usual."
    else:
        return
    await _message(session, order, driver, text, notifier)
    await _refresh_accepted_offer(session, order, notifier)


# ---------------------------------------------------------------- drivers recorded by the dispatcher, and handing over


async def _order_for_driver_action(session: AsyncSession, order_id: int) -> tuple[Order, Driver]:
    """An order being delivered by a driver the dispatcher records for (no Telegram)."""
    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    if order is None:
        raise LookupError
    if order.status not in ACTIVE_STATUSES or order.driver_id is None:
        raise OrderActionError("Only an order a driver is delivering can be updated")
    driver = await session.get(Driver, order.driver_id)
    if not driver.recorded_by_dispatcher:
        raise OrderActionError(f"{driver.name} uses Telegram and updates the order there")
    return order, driver


def off_duty_message(driver: Driver) -> str:
    return f"{driver.name} is off duty. Switch them on duty on the Drivers page first, or choose another driver."


async def _active_driver(session: AsyncSession, driver_id: int) -> Driver:
    driver = await session.get(Driver, driver_id)
    if driver is None or not driver.is_active:
        raise OrderActionError("That driver doesn't exist or is inactive")
    return driver


async def record_given(session: AsyncSession, order_id: int, driver_id: int, user_id: int, notifier: Notifier) -> Order:
    """The dispatcher gave a waiting order to a driver they record for (sent outside the app)."""
    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    if order is None:
        raise LookupError
    if order.status != OrderStatus.PENDING:
        raise OrderActionError("Only an order waiting for a driver can be given to one")
    driver = await _active_driver(session, driver_id)
    if not driver.recorded_by_dispatcher:
        raise OrderActionError(f"{driver.name} uses Telegram: choose them in \"Send to\" instead")
    open_offers = (
        await session.scalars(
            select(OrderOffer).where(OrderOffer.order_id == order.id, OrderOffer.result.is_(None)).options(selectinload(OrderOffer.driver))
        )
    ).all()
    for offer in open_offers:
        offer.result = OfferResult.EXPIRED
        offer.responded_at = utcnow()
    session.add(_event(order, OrderStatus.ACCEPTED, user_id=user_id, note=f"Given to {driver.name} (recorded by the dispatcher)"))
    order.status = OrderStatus.ACCEPTED
    order.driver_id = driver.id
    await session.commit()
    for offer in open_offers:
        await notifier.refresh(offer.driver, order, offer)
    await planning.replan(session, driver.id, new_order_id=order.id)
    return order


async def record_driver_eta(session: AsyncSession, order_id: int, minutes: int, user_id: int) -> Order:
    """The driver told the dispatcher when they'll get there ("after 15 minutes")."""
    order, driver = await _order_for_driver_action(session, order_id)
    now = utcnow()
    order.driver_eta_at = now + timedelta(minutes=minutes)
    order.driver_eta_answered_at = now
    order.driver_eta_or_later = False
    session.add(_event(order, order.status, user_id=user_id, note=f"{driver.name} says ~{minutes} min (recorded by the dispatcher)"))
    await session.commit()
    return order


async def record_asked_eta(session: AsyncSession, order_id: int, user_id: int) -> Order:
    """The dispatcher asked the driver (outside the app) when they'll get there; "Driver says N min" is the answer."""
    order, driver = await _order_for_driver_action(session, order_id)
    order.eta_asked_at = utcnow()
    session.add(_event(order, order.status, user_id=user_id, note=f"Asked {driver.name} for an ETA (recorded by the dispatcher)"))
    await session.commit()
    return order


async def record_arrived(session: AsyncSession, order_id: int, user_id: int) -> Order:
    """The driver told the dispatcher they're there ("here")."""
    order, driver = await _order_for_driver_action(session, order_id)
    if order.status == OrderStatus.AT_STOP:
        raise OrderActionError("Already recorded as arrived")
    session.add(_event(order, OrderStatus.AT_STOP, user_id=user_id, note=f"{driver.name} arrived (recorded by the dispatcher)"))
    order.status = OrderStatus.AT_STOP
    await session.commit()
    await planning.replan(session, driver.id)
    return order


async def record_delivered(session: AsyncSession, order_id: int, user_id: int) -> Order:
    """The driver told the dispatcher the order is delivered."""
    order, driver = await _order_for_driver_action(session, order_id)
    session.add(_event(order, OrderStatus.COMPLETED, user_id=user_id, note=f"Delivered by {driver.name} (recorded by the dispatcher)"))
    order.status = OrderStatus.COMPLETED
    await session.commit()
    await planning.replan(session, driver.id)
    return order


async def give_to_driver(session: AsyncSession, order_id: int, driver_id: int, user_id: int, notifier: Notifier) -> Order:
    """Hand an accepted order the driver hasn't reached yet to another driver, e.g. at the end of a shift. A Telegram
    driver losing it is told and their messages about it are deleted; one getting it receives the order card."""
    order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    if order is None:
        raise LookupError
    if order.status not in (OrderStatus.ACCEPTED, OrderStatus.EN_ROUTE):
        raise OrderActionError("Only an order the driver hasn't reached yet can be given to another driver")
    new = await _active_driver(session, driver_id)
    if new.id == order.driver_id:
        raise OrderActionError(f"{new.name} already has this order")
    if not new.recorded_by_dispatcher and new.telegram_user_id is None:
        raise OrderActionError(f"{new.name} hasn't connected Telegram")
    if not new.recorded_by_dispatcher and not new.on_duty:
        raise OrderActionError(off_duty_message(new))
    old = await session.get(Driver, order.driver_id)
    session.add(_event(order, OrderStatus.ACCEPTED, user_id=user_id, note=f"Given to {new.name} (was {old.name})"))
    order.status = OrderStatus.ACCEPTED
    order.driver_id = new.id
    # The old driver's promises no longer hold
    order.eta_reported_at = order.eta_at = None
    order.eta_asked_at = order.driver_eta_at = order.driver_eta_answered_at = None
    order.driver_eta_or_later = False
    offer = OrderOffer(order_id=order.id, driver_id=new.id, result=OfferResult.ACCEPTED, responded_at=utcnow())
    session.add(offer)
    await session.commit()

    if old.telegram_user_id and not old.recorded_by_dispatcher:
        await _clean_up_chat(session, order.id, notifier, keep_offer_ids={offer.id})
        await notifier.message(old, f"↪️ <b>Order #{order.shown_number} was given to another driver.</b> You don't need to deliver it.")
    if new.telegram_user_id and not new.recorded_by_dispatcher:
        try:
            offer.telegram_message_id = await notifier.send_offer(new, order, offer, None)
            await session.commit()
        except DeliveryError as err:
            log.warning("Can't reach driver %s for order %s: %s", new.id, order.id, err)
    for driver_id, new_order in ((old.id, None), (new.id, order.id)):
        change = await planning.replan(session, driver_id, new_order_id=new_order)
        await route_changed(session, driver_id, change, notifier)
        await send_delivery_list(session, driver_id, notifier)
    return order

