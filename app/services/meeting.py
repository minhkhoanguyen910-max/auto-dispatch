"""Meeting points (Plan 4): a driver asks to meet the customer at a spot instead of the door.

The driver sends a spot from Telegram, or "I'm parked here" after Arrived. `check()` measures it:
- the customer's walk from the door to the spot on the walking map: at most MAX_WALK_M;
- what it saves the driver on the whole trip, from the previous stop A to the next stop C, with Google's times with
  live traffic (OSRM's times have no traffic and are too low in town centres, so they're never the saving):
    door = A -> B -> C, B being the nearest point the car reaches for the address, plus the driver's walk to the
           door and back when the address isn't on a road (e.g. a pedestrian zone)
    spot = A -> X -> C; the customer walks while the driver drives, so their walk doesn't count
  The last stop has no C. A is the driver's GPS position for their next stop;
- how long until the driver gets there, since the customer should hear about it at least NOTICE before.
Everything here only measures and warns; the driver decides, and the dispatcher asks the customer.

`stop_geometry()` / `stop_locations()`: where a stop is: the agreed meeting point if there is one (and where it was
delivered), else the address. Planning and the driver's list use this rule everywhere.
`customer_message()`: what the dispatcher sends the customer (copied, like "My reply"; no automatic SMS).
`usual_spot()`: the spot that worked before for this customer at this destination ("Same place as last time").
"""

import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.models import ACTIVE_STATUSES, Driver, MeetingRequest, MeetingSpot, MeetingStatus, Order, OrderStatus
from app.services import eta, roads, travel
from app.services.travel import ROAD_FACTOR, LatLng, straight_line_m

# The customer walks at most this far (walking map, meters) to a spot chosen in advance; "I'm parked here" only warns
MAX_WALK_M = 250
# The dispatcher's message should reach the customer this long before the driver gets to the spot
NOTICE = timedelta(minutes=10)
# The car has to stop at the spot: a pin farther than this from the road map isn't on a road
MAX_SPOT_OFF_ROAD_M = 50
# A door farther than this from the nearest road: the driver walks to it and back
DOOR_OFF_ROAD_M = 30
# Walking pace when the walking map has no route to the door (m/s)
WALK_SPEED = 1.3
# Waiting for the dispatcher or the customer
OPEN = (MeetingStatus.REQUESTED, MeetingStatus.ASKED)
# The stop is at the meeting point instead of the door (and was, once delivered there)
AT_SPOT = (MeetingStatus.AGREED, MeetingStatus.DELIVERED)
# After Arrived at an agreed spot, the bot asks "Customer not here?" if the order isn't delivered within this
CUSTOMER_WAIT = timedelta(minutes=5)
# Doors this close count as the same destination ("12 Cambridge Rd" and "12 Cambridge Road, Hastings")
SAME_DESTINATION_M = 50
# A customer who declines this many times always gets delivery to the door
DECLINES_FOR_DOOR = 2
# "I'm parked here": the driver waits this long for the customer, then takes it to the door
PARKED_WAIT_MINUTES = 5


class SpotProblem(Exception):
    """The spot can't be used (not on a road, too far for the customer, ...); the driver can send another."""


@dataclass
class Check:
    """A measured spot, kept by the bot (as plain values) until the driver confirms it."""

    lat: float
    lng: float
    name: str  # the street; "" when the map has no name
    parked_here: bool
    walk_meters: int
    walk_seconds: int
    # What it saves the driver, as a range; None when not known (no GPS position, or "I'm parked here")
    saving_min_seconds: int | None = None
    saving_max_seconds: int | None = None
    # How long until the driver gets to the spot; None when not known
    notice_seconds: int | None = None

    @property
    def saving_seconds(self) -> int | None:
        """The middle of the range, which the minimum saving is judged on."""
        if self.saving_min_seconds is None or self.saving_max_seconds is None:
            return None
        return (self.saving_min_seconds + self.saving_max_seconds) // 2

    @property
    def small_saving(self) -> bool:
        return self.saving_seconds is not None and self.saving_seconds < settings.meeting_min_saving_minutes * 60

    @property
    def short_notice(self) -> bool:
        return self.notice_seconds is not None and self.notice_seconds < NOTICE.total_seconds()


def stop_geometry():
    """SQL for where an order's stop is (geometry): its agreed meeting point, else the address."""
    spot = (
        select(func.geometry(MeetingSpot.location))
        .join(MeetingRequest, MeetingRequest.spot_id == MeetingSpot.id)
        .where(MeetingRequest.order_id == Order.id, MeetingRequest.status.in_(AT_SPOT))
        .correlate(Order)
        .scalar_subquery()
    )
    return func.coalesce(spot, func.geometry(Order.dropoff_location))


async def stop_locations(session: AsyncSession, driver_id: int) -> dict[int, LatLng | None]:
    """Each of the driver's active orders: where the stop is (the agreed meeting point, else the address)."""
    loc = stop_geometry()
    rows = await session.execute(
        select(Order.id, func.ST_Y(loc), func.ST_X(loc)).where(Order.driver_id == driver_id, Order.status.in_(ACTIVE_STATUSES))
    )
    return {order_id: (lat, lng) if lat is not None else None for order_id, lat, lng in rows}


def point_of(value) -> LatLng | None:
    """A Point column's (lat, lng), from what SQLAlchemy holds: the "SRID=4326;POINT(lng lat)" text it was set to, or
    the database's WKB (no shapely needed for one point)."""
    if value is None:
        return None
    if isinstance(value, str):
        m = re.search(r"POINT\s*\(\s*(\S+)\s+(\S+)\s*\)", value)
        return (float(m.group(2)), float(m.group(1))) if m else None
    data = value.data
    raw = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
    endian = "<" if raw[0] == 1 else ">"
    (kind,) = struct.unpack(endian + "I", raw[1:5])
    start = 9 if kind & 0x20000000 else 5  # EWKB: an SRID follows the type
    x, y = struct.unpack(endian + "dd", raw[start : start + 16])
    return y, x


def navigation_link(lat: float, lng: float) -> str:
    """Opens turn-by-turn directions in Google Maps on the driver's phone."""
    return f"https://www.google.com/maps/dir/?api=1&destination={lat:.6f},{lng:.6f}"


# A typed position ("50.8562, 0.5810") or one in a Google Maps link: a place's own position (!3d..!4d..) first,
# then the map's centre (@lat,lng) or a search (?q=lat,lng, query=, ll=)
_NUMBER = r"(-?\d{1,3}\.\d+)"
_POSITIONS = [
    re.compile(rf"!3d{_NUMBER}!4d{_NUMBER}"),
    re.compile(rf"@{_NUMBER},{_NUMBER}"),
    re.compile(rf"[?&](?:q|query|ll|destination)=(?:loc:)?{_NUMBER}(?:,|%2C)\s*{_NUMBER}"),
    re.compile(rf"^\s*{_NUMBER}\s*[,;\s]\s*{_NUMBER}\s*$"),
]
# Short share links (maps.app.goo.gl/...) only say where they lead once opened
_SHORT_LINK = re.compile(r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps)/\S+")


def _position_in(text: str) -> LatLng | None:
    for pattern in _POSITIONS:
        if m := pattern.search(text):
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
    return None


async def read_position(text: str) -> LatLng | None:
    """A position the driver typed or pasted instead of sending a location (e.g. from Telegram on a computer, which
    can't send one): "50.8562, 0.5810", or a Google Maps link."""
    if found := _position_in(text):
        return found
    if short := _SHORT_LINK.search(text):
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.get(short.group(0))
            return _position_in(str(resp.url)) or _position_in(resp.text[:200_000])
        except httpx.HTTPError:
            return None
    return None


def map_link(lat: float, lng: float) -> str:
    return f"https://maps.google.com/?q={lat:.6f},{lng:.6f}"


def customer_message(order: Order, m: MeetingRequest, customer_name: str | None, lat: float, lng: float) -> str:
    """The ready-made message the dispatcher copies and sends the customer. The time is route planning's arrival
    when it's on; without it there's no time (the dispatcher can add one)."""
    hi = f"Hi {customer_name.split()[0]}" if customer_name and customer_name.split() else "Hi"
    where = m.spot.name or "this spot"
    walk = f"about {max(1, round((m.walk_seconds or 0) / 60))} min walk ({m.walk_meters} m) from your address"
    if m.parked_here:
        return (
            f"{hi}, your driver is here now with your delivery (order #{order.shown_number}), parked at {where}, {walk}: "
            f"{map_link(lat, lng)} . Could you come out to meet them? They'll wait about {PARKED_WAIT_MINUTES} minutes."
        )
    when = (
        f" They'll be there at about {eta.format_local(order.planned_arrival_at)}."
        if settings.route_optimization and order.planned_arrival_at
        else ""
    )
    return (
        f"{hi}, it's about your delivery (order #{order.shown_number}). The driver can't easily get to your door. "
        f"Could you meet them at {where}? It's {walk}: {map_link(lat, lng)} .{when} Please reply YES or NO."
    )


async def usual_spot(session: AsyncSession, order: Order) -> MeetingSpot | None:
    """Where this customer was met before when delivering to this destination (a door within SAME_DESTINATION_M), and
    it worked (delivered there). Customers' addresses change, so a spot is only offered for the destination it was
    used for. The spot that worked most often comes first, then the most recent."""
    if order.customer_id is None or order.dropoff_location is None:
        return None
    earlier = aliased(Order)
    door = select(Order.dropoff_location).where(Order.id == order.id).scalar_subquery()
    return await session.scalar(
        select(MeetingSpot)
        .join(MeetingRequest, MeetingRequest.spot_id == MeetingSpot.id)
        .join(earlier, earlier.id == MeetingRequest.order_id)
        .where(
            earlier.customer_id == order.customer_id,
            earlier.id != order.id,
            MeetingRequest.status == MeetingStatus.DELIVERED,
            func.ST_DWithin(earlier.dropoff_location, door, SAME_DESTINATION_M),
        )
        .group_by(MeetingSpot.id)
        .order_by(func.count().desc(), func.max(MeetingRequest.created_at).desc())
        .limit(1)
    )


async def spot_location(session: AsyncSession, spot_id: int) -> LatLng:
    loc = func.geometry(MeetingSpot.location)
    row = (await session.execute(select(func.ST_Y(loc), func.ST_X(loc)).where(MeetingSpot.id == spot_id))).one()
    return row[0], row[1]


async def door_location(session: AsyncSession, order_id: int) -> LatLng | None:
    door = func.geometry(Order.dropoff_location)
    row = (await session.execute(select(func.ST_Y(door), func.ST_X(door)).where(Order.id == order_id))).one()
    return (row[0], row[1]) if row[0] is not None else None


async def driver_position(session: AsyncSession, driver_id: int, fresh: timedelta | None = None) -> LatLng | None:
    """The driver's GPS position; with `fresh`, only if their phone sent it within that long."""
    here = func.geometry(Driver.last_location)
    query = select(func.ST_Y(here), func.ST_X(here)).where(Driver.id == driver_id)
    if fresh is not None:
        query = query.where(Driver.last_seen_at >= func.now() - fresh)
    row = (await session.execute(query)).one_or_none()
    return (row[0], row[1]) if row and row[0] is not None else None


async def check(session: AsyncSession, order: Order, driver: Driver, spot: LatLng, parked_here: bool, now: datetime) -> Check:
    """Measure a spot for the order. Raises SpotProblem, or roads.RoutingUnavailable when OSRM isn't answering."""
    door = await door_location(session, order.id)
    road = await roads.nearest_road(spot)
    if parked_here:
        # Wherever the driver stopped (a car park, a lay-by): the spot is where they are
        x, name = spot, road.name if road else ""
    else:
        if road is None or road.meters > MAX_SPOT_OFF_ROAD_M:
            raise SpotProblem("That spot isn't on a road a car can reach. Move the pin onto the street.")
        x, name = road.point, road.name
    walk = await roads.walk(door, x)
    if parked_here:
        # The driver is already there and can't park anywhere better: no limit, a long walk is only a warning. Off the
        # walking map (e.g. a car park it doesn't know): estimated from the straight line
        if walk is None:
            meters = straight_line_m(door, x) * ROAD_FACTOR
            walk = roads.Route([], meters, meters / WALK_SPEED)
    elif walk is None:
        raise SpotProblem("There's no walking route from the address to that spot on the map.")
    elif walk.meters > MAX_WALK_M:
        raise SpotProblem(f"The customer would walk {walk.meters:.0f} m; at most {MAX_WALK_M} m. Pick a spot closer to the address.")
    result = Check(x[0], x[1], name, parked_here, round(walk.meters), round(walk.seconds))
    if parked_here:
        return result  # the driver is already there: nothing left to save or to warn about

    a, c, leave_a = await _neighbours(session, order, driver, now)
    if a is None:
        return result
    door_road = await roads.nearest_road(door)
    b = door_road.point if door_road else door
    door_walk = 0.0
    if door_road and door_road.meters > DOOR_OFF_ROAD_M:
        to_door = await roads.walk(b, door)
        door_walk = 2 * (to_door.seconds if to_door else door_road.meters / WALK_SPEED)
    first = (await travel.matrix(session, [a], [b, x], traffic=True))[0]
    legs = [*first]
    door_s, spot_s = first[0].seconds + door_walk, first[1].seconds
    if c is not None:
        to_c = await travel.matrix(session, [b, x], [c], traffic=True)
        legs += [to_c[0][0], to_c[1][0]]
        door_s += to_c[0][0].seconds
        spot_s += to_c[1][0].seconds
    # Google's single numbers as a range: a minute either way plus 10% of the trip, doubled for estimates
    margin = 60 + 0.1 * spot_s
    if any(leg.source != "google" for leg in legs):
        margin *= 2
    saving = door_s - spot_s
    result.saving_min_seconds = round(saving - margin)
    result.saving_max_seconds = round(saving + margin)
    if leave_a is not None:
        result.notice_seconds = round((leave_a - now).total_seconds() + first[1].seconds)
    return result


async def _neighbours(
    session: AsyncSession, order: Order, driver: Driver, now: datetime
) -> tuple[LatLng | None, LatLng | None, datetime | None]:
    """Around this stop in the driver's current order of stops (planned, or nearest first): where they come from
    (A: the previous stop, or their GPS for the next stop), where they go next (C, None for the last stop), and when
    they leave A (None when it can't be told)."""
    where = await stop_locations(session, driver.id)
    shown = [i for i in driver.list_order_ids or [] if i in where]
    sequence = shown + sorted(i for i in where if i not in shown)
    k = sequence.index(order.id)
    c = next((where[i] for i in sequence[k + 1 :] if where[i]), None)
    gps = await driver_position(session, driver.id)
    before = [i for i in sequence[:k] if where[i]]
    if not before:
        return gps, c, now if gps else None
    service = timedelta(seconds=await eta.stop_seconds(session, now))
    previous = before[-1]
    planned = (await session.get(Order, previous)).planned_arrival_at
    if settings.route_optimization and planned is not None:
        return where[previous], c, max(now, planned + service)
    # Nearest first (or no plan yet): add up the legs from the driver's position
    if gps is None:
        return where[previous], c, None
    t, here = now, gps
    for i in before:
        leg = (await travel.matrix(session, [here], [where[i]], traffic=here == gps))[0][0]
        at_stop = (await session.get(Order, i)).status == OrderStatus.AT_STOP
        t += (timedelta(0) if at_stop else timedelta(seconds=leg.seconds)) + service
        here = where[i]
    return where[previous], c, t
