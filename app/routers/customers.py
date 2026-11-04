import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import Customer, CustomerAddress, CustomerPhone, Order, OrderStatus
from app.schemas import CustomerIn, CustomerOut, CustomerPhoneOut, ImportResult
from app.services.addresses import recent_addresses
from app.services.customers import ImportFileError, import_customers, normalize_phone

router = APIRouter(prefix="/customers", tags=["customers"], dependencies=[Depends(get_current_user)])

MAX_IMPORT_BYTES = 5 * 1024 * 1024


async def _to_out(session: AsyncSession, customers: list[Customer]) -> list[CustomerOut]:
    """Customers with their recent addresses and order counts (canceled orders don't count)."""
    ids = [c.id for c in customers]
    recent = await recent_addresses(session, ids)
    stats = {
        row[0]: (row[1], row[2])
        for row in await session.execute(
            select(Order.customer_id, func.count(), func.max(Order.created_at))
            .where(Order.customer_id.in_(ids), Order.status != OrderStatus.CANCELED)
            .group_by(Order.customer_id)
        )
    }
    return [
        CustomerOut(
            id=c.id,
            name=c.name,
            phones=[CustomerPhoneOut.model_validate(p) for p in c.phones],
            notes=c.notes,
            is_active=c.is_active,
            always_to_door=c.always_to_door,
            meeting_declines=c.meeting_declines,
            recent_addresses=recent[c.id],
            order_count=stats.get(c.id, (0, None))[0],
            last_order_at=stats.get(c.id, (0, None))[1],
            created_at=c.created_at,
        )
        for c in customers
    ]


async def _load(session: AsyncSession, customer_id: int) -> Customer:
    customer = await session.scalar(
        select(Customer).where(Customer.id == customer_id).execution_options(populate_existing=True)
    )
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer


async def _phones(session: AsyncSession, customer: Customer, body: CustomerIn) -> list[CustomerPhone]:
    """The customer's phones as sent. A number the customer already has keeps its row (only label
    and position change), so removing and re-adding a number doesn't clash with itself."""
    numbers: dict[str, str | None] = {}  # number -> label, in the order given
    for p in body.phones:
        try:
            number = normalize_phone(p.phone)
        except ValueError as err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))
        if number in numbers:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{p.phone.strip()} is listed twice")
        numbers[number] = (p.label or "").strip() or None
    if numbers:
        taken = select(CustomerPhone.phone, Customer.name).join(Customer).where(CustomerPhone.phone.in_(numbers))
        if customer.id:
            taken = taken.where(CustomerPhone.customer_id != customer.id)
        if clash := (await session.execute(taken)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, f"{clash.name} already has the number {clash.phone}")
    existing = {p.phone: p for p in customer.phones}
    phones = []
    for position, (number, label) in enumerate(numbers.items()):
        phone = existing.get(number) or CustomerPhone(phone=number)
        phone.label, phone.position = label, position
        phones.append(phone)
    return phones


async def _save(session: AsyncSession, customer: Customer, body: CustomerIn) -> None:
    phones = await _phones(session, customer, body)
    customer.name = body.name.strip()
    customer.phones = phones
    customer.notes = (body.notes or "").strip() or None
    customer.is_active = body.is_active
    if body.always_to_door is not None:
        if customer.always_to_door and not body.always_to_door:
            customer.meeting_declines = 0  # a fresh start, or the next decline would switch it straight back on
        customer.always_to_door = body.always_to_door
    session.add(customer)
    await session.commit()


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    q: str = Query("", max_length=200),
    include_inactive: bool = False,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[CustomerOut]:
    """Customers by name, or the ones whose name, phone or address contains `q`."""
    query = select(Customer)
    if not include_inactive:
        query = query.where(Customer.is_active)
    if q := q.strip():
        matches = [
            Customer.name.icontains(q, autoescape=True),
            Customer.addresses.any(CustomerAddress.address.icontains(q, autoescape=True)),
            exists().where(Order.customer_id == Customer.id, Order.dropoff_address.icontains(q, autoescape=True)),
        ]
        # Phones are stored as +44..., so "07911 123" searches for "7911123"
        if len(digits := re.sub(r"\D", "", q).lstrip("0")) >= 3:
            matches.append(Customer.phones.any(CustomerPhone.phone.contains(digits, autoescape=True)))
        query = query.where(or_(*matches))
    customers = list(await session.scalars(query.order_by(func.lower(Customer.name), Customer.id).limit(limit)))
    return await _to_out(session, customers)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: int, session: AsyncSession = Depends(get_session)) -> CustomerOut:
    return (await _to_out(session, [await _load(session, customer_id)]))[0]


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(body: CustomerIn, session: AsyncSession = Depends(get_session)) -> CustomerOut:
    customer = Customer(phones=[], addresses=[])
    await _save(session, customer, body)
    return (await _to_out(session, [await _load(session, customer.id)]))[0]


@router.put("/{customer_id}", response_model=CustomerOut)
async def update_customer(customer_id: int, body: CustomerIn, session: AsyncSession = Depends(get_session)) -> CustomerOut:
    customer = await _load(session, customer_id)
    await _save(session, customer, body)
    return (await _to_out(session, [await _load(session, customer.id)]))[0]


@router.post("/import", response_model=ImportResult)
async def import_file(
    request: Request,
    filename: str = Query(max_length=255),
    dry_run: bool = True,
    session: AsyncSession = Depends(get_session),
) -> ImportResult:
    """The file is the request body (CSV or .xlsx). Defaults to a dry run that only reports what
    importing would do."""
    content = await request.body()
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is larger than 5 MB")
    try:
        return await import_customers(session, content, filename, dry_run)
    except ImportFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))
