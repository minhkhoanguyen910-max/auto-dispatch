import enum
from datetime import date, datetime
from decimal import Decimal

from geoalchemy2 import Geography
from sqlalchemy import ARRAY, UniqueConstraint, BigInteger, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EN_ROUTE = "EN_ROUTE"
    AT_STOP = "AT_STOP"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


ACTIVE_STATUSES = (OrderStatus.ACCEPTED, OrderStatus.EN_ROUTE, OrderStatus.AT_STOP)
FINAL_STATUSES = (OrderStatus.COMPLETED, OrderStatus.CANCELED)

# Allowed status changes. COMPLETED and CANCELED are final.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.ACCEPTED, OrderStatus.CANCELED},
    OrderStatus.ACCEPTED: {OrderStatus.EN_ROUTE, OrderStatus.CANCELED},
    OrderStatus.EN_ROUTE: {OrderStatus.AT_STOP, OrderStatus.CANCELED},
    OrderStatus.AT_STOP: {OrderStatus.COMPLETED, OrderStatus.CANCELED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELED: set(),
}


class OfferResult(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"


class MeetingStatus(str, enum.Enum):
    """Where a driver's request to meet the customer away from the door stands (Plan 4)."""

    REQUESTED = "REQUESTED"  # the driver asked; the dispatcher hasn't sent it to the customer yet
    ASKED = "ASKED"  # the dispatcher sent it; waiting for the customer
    AGREED = "AGREED"  # the stop is now the meeting point
    DECLINED = "DECLINED"  # the customer said no: deliver to the door
    EXPIRED = "EXPIRED"  # not answered by the deadline: deliver to the door
    NOT_THERE = "NOT_THERE"  # the customer didn't come within 5 min: the driver went to the door
    DELIVERED = "DELIVERED"  # delivered at the meeting point


Point = Geography(geometry_type="POINT", srid=4326)


class User(Base):
    """Web page user (dispatcher) who enters orders."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    # Admins can manage users; everyone else only works with orders and drivers
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(32))
    # Matches the device "identifier" configured in the Traccar Client app
    traccar_unique_id: Mapped[str] = mapped_column(String(64), unique=True)
    # Chat the bot sends order offers to; set when the driver opens their link
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    # One-time code for t.me/<bot>?start=<code>; cleared once used
    link_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    # No Telegram: the dispatcher records what the driver does (given, "arrives in N min", delivered) on the web, e.g. to
    # test the app alongside real work. Never offered orders automatically
    recorded_by_dispatcher: Mapped[bool] = mapped_column(default=False, server_default="false")
    # On duty: Automatic gives them orders, and "Send to" can choose them. Off duty: no new orders; the ones they
    # have stay theirs
    on_duty: Mapped[bool] = mapped_column(default=False, server_default="false")
    # The bot's latest "Your deliveries" message, deleted when a newer list is sent, and its
    # stops (order ids) in the order shown, kept when the list is updated without re-sorting
    list_message_id: Mapped[int | None] = mapped_column(BigInteger)
    list_order_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    # When route planning last calculated this driver's route (the stop sequence is `list_order_ids`)
    route_planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_location = mapped_column(Point, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # From the same GPS fix: heading in degrees (0 = north, clockwise) and speed, for the live map
    last_course: Mapped[float | None]
    last_speed_kmh: Mapped[float | None]
    # Live map: [lat, lng] points along the roads from the previous fix to this one (None: draw a straight line)
    last_path: Mapped[list[list[float]] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    """Something drivers deliver. Never deleted, only deactivated, since past orders refer to it."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(64), unique=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Customer(Base):
    """Who an order is for. Only dispatchers see customers; drivers get just the drop-off address.
    Never deleted, only deactivated, since past orders refer to it."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # For dispatchers only, never sent to drivers
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    # Meeting points (Plan 4): drivers' requests are refused straight away. Set after 2 declines; the
    # dispatcher can change it
    always_to_door: Mapped[bool] = mapped_column(default=False, server_default="false")
    meeting_declines: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    phones: Mapped[list["CustomerPhone"]] = relationship(
        lazy="selectin", order_by="CustomerPhone.position", cascade="all, delete-orphan"
    )
    addresses: Mapped[list["CustomerAddress"]] = relationship(
        lazy="selectin", order_by="[CustomerAddress.is_default.desc(), CustomerAddress.id]", cascade="all, delete-orphan"
    )

    @property
    def phone(self) -> str | None:
        """The first (main) phone number."""
        return self.phones[0].phone if self.phones else None


class CustomerPhone(Base):
    """One of a customer's phone numbers. A number belongs to one customer only, so it identifies
    them (search, import)."""

    __tablename__ = "customer_phones"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(50))  # "Mobile", "Work", ...
    # International form ("+447911123456"), so the same number written differently is still one customer
    phone: Mapped[str] = mapped_column(String(32), unique=True)
    # Order shown; the first is the main number
    position: Mapped[int] = mapped_column(default=0)


class CustomerAddress(Base):
    """An address known for a customer before any order went there, e.g. from an import. Suggested
    for their orders alongside their recent drop-offs; orders store their own address."""

    __tablename__ = "customer_addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(50))  # "Home", "Office", ...
    address: Mapped[str] = mapped_column(Text)
    # From the import, or filled in by Google
    location = mapped_column(Point, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("order_day", "day_number", name="uq_orders_day_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Numbered per day (APP_TIMEZONE). The system's full number is `ref` ("2026-11-03-003"), counting every order;
    # dispatchers and drivers see `shown_number` ("#3"), which counts only the day's orders that aren't canceled:
    # canceling #2 makes #3 the new #2. A canceled order keeps the number it last had
    order_day: Mapped[date] = mapped_column(Date)
    day_number: Mapped[int] = mapped_column(Integer)
    shown_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING, index=True
    )
    # Required for new orders; older orders have none
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    dropoff_address: Mapped[str] = mapped_column(Text)
    dropoff_location = mapped_column(Point, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"), index=True)
    # Set when the dispatcher picks "Send to: <driver>"; then only that driver is offered the order
    target_driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"))
    # Start of the current dispatch round. Each driver is offered the order at most once per round;
    # "Retry" starts a new round so everyone can be asked again.
    dispatch_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Driver's "arriving in N min" from Telegram: when they sent it and the arrival time it gives
    eta_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Customer in a rush / must have it by a time; route planning puts these first (Plan 1)
    is_urgent: Mapped[bool] = mapped_column(default=False, server_default="false")
    deliver_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # deliver_by is the automatic limit (placed + MAX_WAIT_MINUTES), not a time someone chose
    deliver_by_auto: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Set by route planning while a driver has the order: its stop number and planned arrival (a range,
    # ETA to latest); `eta_estimated` when the times are estimates rather than Google's
    route_position: Mapped[int | None]
    planned_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_arrival_latest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_estimated: Mapped[bool] = mapped_column(default=False, server_default="false")
    # "Ask driver for ETA" from the web: when asked, and the driver's answer (arrival time, and whether they
    # said "10+ min", i.e. at least that)
    eta_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    driver_eta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    driver_eta_answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    driver_eta_or_later: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer | None] = relationship()
    driver: Mapped[Driver | None] = relationship(foreign_keys=[driver_id])
    target_driver: Mapped[Driver | None] = relationship(foreign_keys=[target_driver_id])
    created_by: Mapped[User | None] = relationship()
    offers: Mapped[list["OrderOffer"]] = relationship(back_populates="order", order_by="OrderOffer.sent_at")
    # Always loaded with the order: Telegram messages and the web page both show them
    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin", order_by="OrderItem.id", cascade="all, delete-orphan")
    events: Mapped[list["OrderEvent"]] = relationship(back_populates="order", order_by="OrderEvent.created_at")
    change_requests: Mapped[list["OrderChangeRequest"]] = relationship(order_by="OrderChangeRequest.created_at")
    meeting: Mapped["MeetingRequest | None"] = relationship(
        back_populates="order", lazy="selectin", cascade="all, delete-orphan"
    )


    @property
    def total(self) -> Decimal:
        return sum((i.line_total for i in self.items), Decimal(0))

    @property
    def ref(self) -> str:
        """The full order number, unique for good: "2026-11-03-003"."""
        return f"{self.order_day.isoformat()}-{self.day_number:03d}"


class OrderItem(Base):
    """One product line on an order. Name and price are copied from the product when the order
    is created, so later catalog changes don't alter existing orders."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int]

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class OrderChangeRequest(Base):
    """A driver telling the dispatcher (via the bot) that something about the order changed,
    e.g. "customer wants 3 instead of 2". Open until a dispatcher edits the order or marks it handled.

    `applied` reports are new quantities the driver entered at the door: already applied to the
    order, open only until a dispatcher has seen them."""

    __tablename__ = "order_change_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    text: Mapped[str] = mapped_column(Text)
    applied: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    driver: Mapped[Driver] = relationship()
    resolved_by: Mapped[User | None] = relationship()


class OrderMessage(Base):
    """A Telegram message about an order other than its offer card: update notices, change reports
    (the bot's and the driver's), ... Deleted when the order is finished, so only one message
    per order stays in the driver's chat."""

    __tablename__ = "order_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderOffer(Base):
    """One row each time an order is offered to a driver over Telegram."""

    __tablename__ = "order_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), index=True)
    # NULL while waiting for the driver's answer
    result: Mapped[OfferResult | None] = mapped_column(Enum(OfferResult, name="offer_result"))
    # Lets the bot edit the offer message later (e.g. "expired")
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="offers")
    driver: Mapped[Driver] = relationship()


class DriverMessage(Base):
    """A message a driver sent the bot (Plan 5): a reply about an order ("10", "here", "done", or anything else) or a
    message about none ("start", "finish", ...). `meaning` is what the bot understood (minutes, arrived, delivered,
    start, finish, other) and `result` what it did ("arrives ~14:35"), or why it couldn't. The web shows each as an
    alert."""

    __tablename__ = "driver_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    text: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(String(16))
    result: Mapped[str | None] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    driver: Mapped["Driver"] = relationship(lazy="selectin")
    order: Mapped["Order | None"] = relationship(lazy="selectin")


class OrderEvent(Base):
    """Audit trail: one row per status change."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus, name="order_status"))
    to_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, name="order_status"))
    # Who made the change: a user (web), a driver (Telegram), or neither (system)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="events")


class MeetingSpot(Base):
    """A place drivers have met customers (Plan 4), suggested first next time. Every spot a driver chooses
    is kept, with how it went."""

    __tablename__ = "meeting_spots"

    id: Mapped[int] = mapped_column(primary_key=True)
    location = mapped_column(Point, nullable=False)
    # Street or place, e.g. "Queens Rd"
    name: Mapped[str | None] = mapped_column(String(200))
    times_chosen: Mapped[int] = mapped_column(default=0, server_default="0")
    times_worked: Mapped[int] = mapped_column(default=0, server_default="0")
    times_not_there: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingRequest(Base):
    """A driver asking to meet the customer at a spot instead of the door (Plan 4). At most one per order:
    the customer is asked only once."""

    __tablename__ = "meeting_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    spot_id: Mapped[int] = mapped_column(ForeignKey("meeting_spots.id"))
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus, name="meeting_status"), default=MeetingStatus.REQUESTED, index=True
    )
    # "I'm parked here" after Arrived: the driver is already waiting, so the dispatcher calls now
    parked_here: Mapped[bool] = mapped_column(default=False, server_default="false")
    # The customer's walk from the door to the spot (walking map)
    walk_meters: Mapped[int | None]
    walk_seconds: Mapped[int | None]
    # What the spot saves the driver on the whole trip, as a range (Google's times); None: not known
    saving_min_seconds: Mapped[int | None]
    saving_max_seconds: Mapped[int | None]
    # The dispatcher's message should reach the customer by then (10 min before the driver gets there).
    # Recalculated when the route changes; None for "I'm parked here"
    send_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the status last moved on from ASKED (agreed, declined, expired, ...), and the dispatcher who set it
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # When the bot asked the driver at the spot "Customer not here?" (5 min after Arrived), so it asks once
    not_here_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="meeting")
    driver: Mapped[Driver] = relationship()
    spot: Mapped[MeetingSpot] = relationship(lazy="selectin")
    resolved_by: Mapped[User | None] = relationship()


class TravelTime(Base):
    """A remembered driving time from one place to another (Google Route Matrix), so the same trip isn't
    paid for twice. Places are rounded to about 50 m ("51.5005,-0.1245"). Times without traffic don't change
    and are kept for weeks; times with live traffic only for minutes."""

    __tablename__ = "travel_times"

    origin: Mapped[str] = mapped_column(String(32), primary_key=True)
    destination: Mapped[str] = mapped_column(String(32), primary_key=True)
    traffic: Mapped[bool] = mapped_column(primary_key=True)
    seconds: Mapped[int]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoutesUsage(Base):
    """How many Route Matrix pairs were requested from Google each day (UTC), for the daily cap."""

    __tablename__ = "routes_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    pairs: Mapped[int] = mapped_column(default=0)


class AppSetting(Base):
    """A setting changed from the web app (e.g. the dispatchers' own wording for replying to customers)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
