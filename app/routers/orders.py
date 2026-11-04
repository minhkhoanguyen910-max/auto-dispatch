from datetime import timedelta
from typing import Literal

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.config import settings
from app.db import get_session
from app.models import (
    FINAL_STATUSES,
    Customer,
    Driver,
    MeetingSpot,
    OfferResult,
    Order,
    OrderChangeRequest,
    OrderEvent,
    OrderItem,
    OrderOffer,
    OrderStatus,
    Product,
    User,
)
from app.schemas import (
    ChangeRequestOut,
    CustomerRef,
    DriverRef,
    LocateIn,
    LocateOut,
    DriverPick,
    MeetingOut,
    MinutesIn,
    OrderCancel,
    OrderCreate,
    OrderDetail,
    OrderItemOut,
    OrderOut,
    OrderUpdate,
    TimelineItem,
)
from app.services import dispatch, meeting
from app.services.addresses import known_location
from app.services.eta import auto_deliver_by, local_time
from app.services.geocoding import locate, locate_detail, point
from app.services.notifier import NullNotifier, TelegramNotifier

router = APIRouter(prefix="/orders", tags=["orders"], dependencies=[Depends(get_current_user)])

# Before the worker's first offer, a new order briefly has no open offer; don't call that "no driver"
NO_DRIVER_GRACE = timedelta(seconds=10)
# An offer accepted the moment it was made: the order was given (Plan 5, or Give to another driver)
GIVEN_AT_ONCE = timedelta(seconds=5)

OFFER_RESULT_TEXT = {
    None: "is deciding",
    OfferResult.DECLINED: "declined",
    OfferResult.EXPIRED: "didn't answer in time",
}


def _ref(driver: Driver | None) -> DriverRef | None:
    return DriverRef.model_validate(driver) if driver else None


async def _coords(session: AsyncSession, order: Order) -> tuple[float | None, float | None]:
    if order.dropoff_location is None:
        return None, None
    loc = func.geometry(Order.dropoff_location)
    return (await session.execute(select(func.ST_Y(loc), func.ST_X(loc)).where(Order.id == order.id))).one()


async def _meeting(session: AsyncSession, order: Order) -> MeetingOut | None:
    m = order.meeting
    if m is None or order.status in FINAL_STATUSES:
        return None
    lat, lng = await meeting.spot_location(session, m.spot_id)
    customer = order.customer
    return MeetingOut(
        status=m.status.value,
        parked_here=m.parked_here,
        spot_name=m.spot.name,
        spot_lat=lat,
        spot_lng=lng,
        walk_meters=m.walk_meters,
        walk_seconds=m.walk_seconds,
        saving_min_seconds=m.saving_min_seconds,
        saving_max_seconds=m.saving_max_seconds,
        send_by=m.send_by,
        created_at=m.created_at,
        asked_at=m.asked_at,
        resolved_at=m.resolved_at,
        customer_message=meeting.customer_message(order, m, customer.name if customer else None, lat, lng),
        customer_declines=customer.meeting_declines if customer else 0,
    )


async def _to_out(session: AsyncSession, order: Order, cls=OrderOut, **extra):
    open_offer = next((o for o in order.offers if o.result is None), None)
    lat, lng = await _coords(session, order)
    return cls(
        id=order.id,
        number=order.shown_number,
        order_day=order.order_day,
        ref=order.ref,
        status=order.status.value,
        customer=CustomerRef.model_validate(order.customer) if order.customer else None,
        is_urgent=order.is_urgent,
        deliver_by=order.deliver_by,
        deliver_by_auto=order.deliver_by_auto,
        route_position=order.route_position,
        route_stops=len(order.driver.list_order_ids or []) if order.route_position and order.driver else None,
        planned_arrival_at=order.planned_arrival_at,
        planned_arrival_latest=order.planned_arrival_latest,
        eta_estimated=order.eta_estimated,
        eta_asked_at=order.eta_asked_at,
        driver_eta_at=order.driver_eta_at,
        driver_eta_answered_at=order.driver_eta_answered_at,
        driver_eta_or_later=order.driver_eta_or_later,
        dropoff_address=order.dropoff_address,
        notes=order.notes,
        dropoff_lat=lat,
        dropoff_lng=lng,
        items=[OrderItemOut.model_validate(i) for i in order.items],
        total=order.total,
        driver=_ref(order.driver),
        target_driver=_ref(order.target_driver),
        offered_to=_ref(open_offer.driver) if open_offer else None,
        offer_expires_at=open_offer.sent_at + timedelta(seconds=settings.offer_timeout_seconds) if open_offer else None,
        no_driver_available=order.status == OrderStatus.PENDING
        and open_offer is None
        and dispatch.utcnow() - order.dispatch_started_at > NO_DRIVER_GRACE,
        eta_reported_at=order.eta_reported_at if order.status == OrderStatus.EN_ROUTE else None,
        eta_at=order.eta_at if order.status == OrderStatus.EN_ROUTE else None,
        open_changes=[
            ChangeRequestOut(id=c.id, driver=c.driver.name, text=c.text, applied=c.applied, created_at=c.created_at)
            for c in order.change_requests
            if c.resolved_at is None and order.status not in FINAL_STATUSES
        ],
        meeting=await _meeting(session, order),
        created_by=order.created_by.name if order.created_by else None,
        created_at=order.created_at,
        updated_at=order.updated_at,
        **extra,
    )


def _with_relations(query):
    return query.options(
        selectinload(Order.customer),
        selectinload(Order.driver),
        selectinload(Order.target_driver),
        selectinload(Order.created_by),
        selectinload(Order.offers).selectinload(OrderOffer.driver),
        selectinload(Order.change_requests).selectinload(OrderChangeRequest.driver),
        selectinload(Order.change_requests).selectinload(OrderChangeRequest.resolved_by),
    )


async def _load(session: AsyncSession, order_id: int) -> Order:
    order = await session.scalar(_with_relations(select(Order).where(Order.id == order_id)).execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


async def _detail(session: AsyncSession, order: Order) -> OrderDetail:
    events = (
        await session.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == order.id)
            .options(selectinload(OrderEvent.order))
            .order_by(OrderEvent.created_at)
        )
    ).all()
    users = {u.id: u.name for u in await session.scalars(select(User).where(User.id.in_({e.user_id for e in events if e.user_id})))}
    drivers = {d.id: d.name for d in await session.scalars(select(Driver).where(Driver.id.in_({e.driver_id for e in events if e.driver_id})))}

    timeline: list[TimelineItem] = []
    for e in events:
        who = users.get(e.user_id) or drivers.get(e.driver_id) or "System"
        if e.from_status is None:
            text = f"Created by {who}"
        elif e.from_status == e.to_status:
            # "Order updated: Water 2 → 3" reads "Order updated by Minh: Water 2 → 3"
            what, _, details = (e.note or "Updated").partition(": ")
            text = f"{what} by {who}" + (f": {details}" if details else "")
        elif e.note and e.note.startswith("Given to"):
            # Orders are given, not accepted (Plan 5): "Given to Daniel (automatic)", "Given to Ann (was Ben) by Minh"
            text = e.note + (f" by {users[e.user_id]}" if e.user_id in users else "")
        else:
            text = f"{e.to_status.value.replace('_', ' ').capitalize()} by {who}"
            if e.note:
                text += f": {e.note}"
        timeline.append(TimelineItem(at=e.created_at, kind="status", text=text))
    for o in order.offers:
        if o.result == OfferResult.ACCEPTED and o.responded_at and abs(o.responded_at - o.sent_at) < GIVEN_AT_ONCE:
            continue  # given, not offered: the "Given to ..." line says it
        timeline.append(TimelineItem(at=o.sent_at, kind="offer", text=f"Offered to {o.driver.name}"))
        # An accepted offer already shows up as the "Accepted by ..." status change
        if o.responded_at and o.result != OfferResult.ACCEPTED:
            timeline.append(TimelineItem(at=o.responded_at, kind="offer", text=f"{o.driver.name} {OFFER_RESULT_TEXT[o.result]}"))
    for c in order.change_requests:
        if c.applied:
            continue
        timeline.append(TimelineItem(at=c.created_at, kind="change", text=f"{c.driver.name} reported a change: {c.text}"))
        if c.resolved_at:
            by = c.resolved_by.name if c.resolved_by else "dispatcher"
            timeline.append(TimelineItem(at=c.resolved_at, kind="change", text=f"Change handled by {by}"))
    timeline.sort(key=lambda t: t.at)
    return await _to_out(session, order, OrderDetail, timeline=timeline)


async def _check_items(
    session: AsyncSession, body: OrderUpdate, allowed_inactive: frozenset[int] = frozenset()
) -> tuple[dict[int, int], dict[int, Product]]:
    """Validated items (product id -> quantity) and their products."""
    if (body.dropoff_lat is None) != (body.dropoff_lng is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give both latitude and longitude, or neither")
    # Same product twice adds up into one line
    quantities: dict[int, int] = {}
    for item in body.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    products = {p.id: p for p in await session.scalars(select(Product).where(Product.id.in_(quantities)))}
    # A product made inactive can stay on an order that already has it
    if any(pid not in products or not (products[pid].is_active or pid in allowed_inactive) for pid in quantities):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One of the products doesn't exist or is inactive")
    return quantities, products


async def _check_customer(session: AsyncSession, body: OrderUpdate, existing: Order | None = None) -> Customer | None:
    if body.customer_id is None:
        # Only an older order that never had a customer may stay without one
        if existing is None or existing.customer_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a customer")
        return None
    customer = await session.get(Customer, body.customer_id)
    # A customer made inactive can stay on an order that already has them
    if customer is None or not (customer.is_active or (existing and existing.customer_id == customer.id)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That customer doesn't exist or is inactive")
    return customer


async def _dropoff(
    session: AsyncSession, body: OrderUpdate, customer: Customer | None, existing: Order | None = None
) -> tuple[str, tuple[float, float] | None]:
    """The drop-off address and its location: the typed coordinates if any; else where this address
    was for the customer's earlier orders (or their import); else Google's, for an address new to them."""
    address = body.dropoff_address.strip()
    if not address:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the drop-off address")
    if body.dropoff_lat is not None:
        return address, (body.dropoff_lat, body.dropoff_lng)
    # Unchanged address without coordinates: the dispatcher removed the location on purpose
    if existing is not None and address == existing.dropoff_address:
        return address, None
    known = await known_location(session, customer.id, address) if customer else None
    return address, known or await locate(address)


def _new_deadline(body: OrderCreate) -> dict:
    """A chosen "deliver by" (or an explicit none), else the automatic waiting limit."""
    if "deliver_by" in body.model_fields_set:
        return {"deliver_by": local_time(body.deliver_by), "deliver_by_auto": False}
    deadline = auto_deliver_by(dispatch.utcnow())
    return {"deliver_by": deadline, "deliver_by_auto": deadline is not None}


def _edited_urgency(body: OrderUpdate, order: Order) -> dict:
    """Urgent / deliver-by after an edit: what was sent, else unchanged."""
    sent = body.model_fields_set
    if "deliver_by" in sent:
        deadline = {"deliver_by": local_time(body.deliver_by), "deliver_by_auto": False}
    else:
        deadline = {"deliver_by": order.deliver_by, "deliver_by_auto": order.deliver_by_auto}
    return {"is_urgent": body.is_urgent if "is_urgent" in sent else order.is_urgent, **deadline}


def _notifier():
    return TelegramNotifier(Bot(settings.telegram_bot_token)) if settings.telegram_bot_token else NullNotifier()


@router.get("", response_model=list[OrderOut])
async def list_orders(
    scope: Literal["active", "done", "all"] = Query("active"),
    customer_id: int | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[OrderOut]:
    final = Order.status.in_(FINAL_STATUSES)
    query = _with_relations(select(Order))
    if scope != "all":
        query = query.where(final if scope == "done" else ~final)
    if customer_id is not None:
        query = query.where(Order.customer_id == customer_id)
    query = query.order_by(Order.updated_at.desc() if scope == "done" else Order.id.desc()).limit(limit)
    return [await _to_out(session, o) for o in await session.scalars(query)]


@router.post("", response_model=OrderDetail, status_code=201)
async def create_order(
    body: OrderCreate, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    if body.target_driver_id is not None:
        target = await session.get(Driver, body.target_driver_id)
        if target is None or not target.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That driver doesn't exist or is inactive")
        if target.telegram_user_id is None and not target.recorded_by_dispatcher:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{target.name} hasn't connected Telegram yet")
        if not target.on_duty and not target.recorded_by_dispatcher:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, dispatch.off_duty_message(target))

    quantities, products = await _check_items(session, body)
    customer = await _check_customer(session, body)
    address, latlng = await _dropoff(session, body, customer)
    order = Order(
        customer_id=customer.id,
        dropoff_address=address,
        notes=(body.notes or "").strip() or None,
        is_urgent=body.is_urgent,
        **_new_deadline(body),
        target_driver_id=body.target_driver_id,
        dropoff_location=point(latlng),
        created_by_id=user.id,
        items=[
            OrderItem(product_id=pid, product_name=products[pid].name, unit_price=products[pid].price, quantity=qty)
            for pid, qty in quantities.items()
        ],
    )
    recorded = target if body.target_driver_id is not None and target.recorded_by_dispatcher else None
    if recorded:
        order.target_driver_id = None  # given straight away instead of offered
    order = await dispatch.create_order(session, order, user.id)
    if recorded:
        # A driver the dispatcher records for: "Send to" them means it's given to them now
        await dispatch.record_given(session, order.id, recorded.id, user.id, NullNotifier())
    return await _detail(session, await _load(session, order.id))


@router.post("/locate", response_model=LocateOut)
async def locate_address(body: LocateIn, session: AsyncSession = Depends(get_session)) -> LocateOut:
    """Where a typed drop-off address is (the customer's earlier orders first, then Google). The form sends the
    location back with the order, so saving doesn't look it up again."""
    address = body.address.strip()
    known = await known_location(session, body.customer_id, address) if body.customer_id is not None else None
    if known:
        return LocateOut(checked=True, latitude=known[0], longitude=known[1])
    found = await locate_detail(address)
    return LocateOut(
        checked=bool(settings.google_maps_api_key),
        latitude=found.latitude if found else None,
        longitude=found.longitude if found else None,
        found_address=found.formatted_address if found else None,
        precision=found.precision if found else None,
    )


@router.get("/{order_id}", response_model=OrderDetail)
async def get_order(order_id: int, session: AsyncSession = Depends(get_session)) -> OrderDetail:
    return await _detail(session, await _load(session, order_id))


@router.post("/{order_id}/cancel", response_model=OrderDetail)
async def cancel_order(
    order_id: int, body: OrderCancel, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    notifier = _notifier()
    try:
        await dispatch.cancel_order(session, order_id, user.id, (body.reason or "").strip() or None, notifier)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    except dispatch.OrderActionError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    finally:
        if isinstance(notifier, TelegramNotifier):
            await notifier.bot.session.close()
    return await _detail(session, await _load(session, order_id))


@router.post("/{order_id}/redispatch", response_model=OrderDetail)
async def redispatch(
    order_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    try:
        await dispatch.redispatch(session, order_id, user.id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    except dispatch.OrderActionError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    return await _detail(session, await _load(session, order_id))


@router.patch("/{order_id}", response_model=OrderDetail)
async def update_order(
    order_id: int, body: OrderUpdate, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    existing = await _load(session, order_id)
    quantities, products = await _check_items(session, body, frozenset(i.product_id for i in existing.items))
    customer = await _check_customer(session, body, existing)
    address, latlng = await _dropoff(session, body, customer, existing)
    notifier = _notifier()
    try:
        await dispatch.update_order(
            session,
            order_id,
            user.id,
            customer=customer,
            dropoff_address=address,
            **_edited_urgency(body, existing),
            dropoff_latlng=latlng,
            notes=(body.notes or "").strip() or None,
            quantities=quantities,
            products=products,
            notifier=notifier,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    except dispatch.OrderActionError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    finally:
        if isinstance(notifier, TelegramNotifier):
            await notifier.bot.session.close()
    return await _detail(session, await _load(session, order_id))


@router.post("/{order_id}/ask-eta", response_model=OrderDetail)
async def ask_eta(
    order_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """Ask the order's driver in Telegram when they'll reach this stop (3 / 5 / 7 / 10+ min)."""
    notifier = _notifier()
    try:
        await dispatch.ask_driver_eta(session, order_id, user.id, notifier)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    except dispatch.OrderActionError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    finally:
        if isinstance(notifier, TelegramNotifier):
            await notifier.bot.session.close()
    return await _detail(session, await _load(session, order_id))


@router.post("/{order_id}/changes/{request_id}/resolve", response_model=OrderDetail)
async def resolve_change(
    order_id: int, request_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    notifier = _notifier()
    try:
        await dispatch.resolve_change_request(session, order_id, request_id, user.id, notifier)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change report not found")
    finally:
        if isinstance(notifier, TelegramNotifier):
            await notifier.bot.session.close()
    return await _detail(session, await _load(session, order_id))


@router.post("/{order_id}/meeting/{answer}", response_model=OrderDetail)
async def meeting_point(
    order_id: int,
    answer: Literal["asked", "agreed", "declined"],
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> OrderDetail:
    """The driver's meeting point request: the message was sent to the customer ("asked"), or the customer agreed or
    declined. The driver is told in Telegram."""
    notifier = _notifier()
    try:
        if answer == "asked":
            await dispatch.meeting_asked(session, order_id, user.id, notifier)
        else:
            await dispatch.meeting_answered(session, order_id, user.id, answer == "agreed", notifier)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    except dispatch.OrderActionError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    finally:
        if isinstance(notifier, TelegramNotifier):
            await notifier.bot.session.close()
    return await _detail(session, await _load(session, order_id))


async def _driver_action(session: AsyncSession, order_id: int, action) -> OrderDetail:
    notifier = _notifier()
    try:
        await action(notifier)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    except dispatch.OrderActionError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    finally:
        if isinstance(notifier, TelegramNotifier):
            await notifier.bot.session.close()
    return await _detail(session, await _load(session, order_id))


@router.post("/{order_id}/record/given", response_model=OrderDetail)
async def record_given(
    order_id: int, body: DriverPick, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """Given to a driver the dispatcher records for (sent outside the app)."""
    return await _driver_action(session, order_id, lambda n: dispatch.record_given(session, order_id, body.driver_id, user.id, n))


@router.post("/{order_id}/record/eta", response_model=OrderDetail)
async def record_eta(
    order_id: int, body: MinutesIn, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """The recorded driver says they'll get there in N minutes."""
    return await _driver_action(session, order_id, lambda n: dispatch.record_driver_eta(session, order_id, body.minutes, user.id))


@router.post("/{order_id}/record/asked-eta", response_model=OrderDetail)
async def record_asked_eta(
    order_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """The dispatcher asked the recorded driver when they'll get there."""
    return await _driver_action(session, order_id, lambda n: dispatch.record_asked_eta(session, order_id, user.id))


@router.post("/{order_id}/record/arrived", response_model=OrderDetail)
async def record_arrived(
    order_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """The recorded driver says they're there ("here")."""
    return await _driver_action(session, order_id, lambda n: dispatch.record_arrived(session, order_id, user.id))


@router.post("/{order_id}/record/delivered", response_model=OrderDetail)
async def record_delivered(
    order_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """The recorded driver says it's delivered."""
    return await _driver_action(session, order_id, lambda n: dispatch.record_delivered(session, order_id, user.id))


@router.post("/{order_id}/give", response_model=OrderDetail)
async def give_to_driver(
    order_id: int, body: DriverPick, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> OrderDetail:
    """Hand an order the driver hasn't reached yet to another driver (e.g. at the end of a shift)."""
    return await _driver_action(session, order_id, lambda n: dispatch.give_to_driver(session, order_id, body.driver_id, user.id, n))

