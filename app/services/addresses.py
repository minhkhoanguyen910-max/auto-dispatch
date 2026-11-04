"""Customers' recent drop-off addresses. Drop-offs aren't fixed (home, work, a friend's, a hotel...),
so there's no address list to maintain: suggestions come from where the customer's orders went,
newest first, then addresses known from an import that no order has used yet."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustomerAddress, Order, OrderStatus
from app.schemas import RecentAddressOut

MAX_RECENT = 5


def address_key(text: str) -> str:
    """Same address however it's spaced or capitalized."""
    return " ".join(text.lower().split())


def _sql_key(column):
    return func.lower(func.regexp_replace(func.btrim(column), r"\s+", " ", "g"))


async def recent_addresses(session: AsyncSession, customer_ids: list[int]) -> dict[int, list[RecentAddressOut]]:
    result: dict[int, list[RecentAddressOut]] = {i: [] for i in customer_ids}
    if not customer_ids:
        return result
    key = _sql_key(Order.dropoff_address)
    loc = func.geometry(Order.dropoff_location)
    # Each address's newest order (canceled ones don't count); the order id breaks ties
    newest = (
        select(Order.customer_id, Order.id, Order.dropoff_address, Order.created_at, func.ST_Y(loc), func.ST_X(loc))
        .where(Order.customer_id.in_(customer_ids), Order.status != OrderStatus.CANCELED)
        .distinct(Order.customer_id, key)
        .order_by(Order.customer_id, key, Order.created_at.desc(), Order.id.desc())
    )
    rows = sorted(await session.execute(newest), key=lambda r: (r.created_at, r.id), reverse=True)
    for customer_id, _, address, used_at, lat, lng in rows:
        result[customer_id].append(RecentAddressOut(address=address, label=None, last_used_at=used_at, latitude=lat, longitude=lng))

    aloc = func.geometry(CustomerAddress.location)
    known = (
        select(CustomerAddress.customer_id, CustomerAddress.address, CustomerAddress.label, func.ST_Y(aloc), func.ST_X(aloc))
        .where(CustomerAddress.customer_id.in_(customer_ids))
        .order_by(CustomerAddress.customer_id, CustomerAddress.is_default.desc(), CustomerAddress.id)
    )
    for customer_id, address, label, lat, lng in await session.execute(known):
        used = next((a for a in result[customer_id] if address_key(a.address) == address_key(address)), None)
        if used:
            # An imported address that orders went to: keep its label ("Home") on the suggestion
            used.label = used.label or label
        else:
            result[customer_id].append(RecentAddressOut(address=address, label=label, last_used_at=None, latitude=lat, longitude=lng))
    return {i: addresses[:MAX_RECENT] for i, addresses in result.items()}


async def known_location(session: AsyncSession, customer_id: int, address: str) -> tuple[float, float] | None:
    """Where this address is, if the customer used it before or it came with an import, so Google
    isn't asked again for the same place."""
    key = address_key(address)
    loc = func.geometry(Order.dropoff_location)
    row = (
        await session.execute(
            select(func.ST_Y(loc), func.ST_X(loc))
            .where(Order.customer_id == customer_id, _sql_key(Order.dropoff_address) == key, Order.dropoff_location.is_not(None))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        aloc = func.geometry(CustomerAddress.location)
        row = (
            await session.execute(
                select(func.ST_Y(aloc), func.ST_X(aloc))
                .where(
                    CustomerAddress.customer_id == customer_id,
                    _sql_key(CustomerAddress.address) == key,
                    CustomerAddress.location.is_not(None),
                )
                .limit(1)
            )
        ).first()
    return (row[0], row[1]) if row else None
