from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    is_admin: bool
    is_active: bool
    created_at: datetime


class LoginIn(BaseModel):
    # Not EmailStr: login only looks the address up, so any text gets the generic "wrong email or password"
    email: str
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=72)  # bcrypt uses at most 72 bytes
    is_admin: bool = False


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8, max_length=72)
    is_admin: bool | None = None
    is_active: bool | None = None


TRACCAR_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


class DriverOut(BaseModel):
    id: int
    name: str
    phone: str | None
    traccar_unique_id: str
    is_active: bool
    # No Telegram: the dispatcher records for this driver on the web
    recorded_by_dispatcher: bool
    # Gets new orders (Automatic, "Send to"); off duty keeps the orders they have
    on_duty: bool
    telegram_connected: bool
    # Personal t.me link while the driver hasn't connected Telegram yet (or got a new link)
    telegram_link: str | None
    online: bool
    last_seen_at: datetime | None
    latitude: float | None
    longitude: float | None
    # Live map: heading in degrees (0 = north), speed, and whether they're moving (faster than walking pace)
    course: float | None
    speed_kmh: float | None
    moving: bool
    # Live map: [lat, lng] points along the roads from the previous GPS fix to this one (None: straight line)
    path: list[list[float]] | None
    created_at: datetime


class RouteOut(BaseModel):
    """Map's Route tool: [lat, lng] points along the roads, driving distance and time without traffic."""

    path: list[list[float]]
    distance_m: float
    duration_s: float


class DriverCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    # Leave empty to get a generated 6-digit identifier
    traccar_unique_id: str | None = Field(default=None, pattern=TRACCAR_ID_PATTERN)
    recorded_by_dispatcher: bool = False


class DriverUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    traccar_unique_id: str | None = Field(default=None, pattern=TRACCAR_ID_PATTERN)
    is_active: bool | None = None
    recorded_by_dispatcher: bool | None = None
    on_duty: bool | None = None


class DriverWords(BaseModel):
    """The words drivers reply with (Plan 5), one list per meaning; numbers always mean minutes."""

    arrived: list[str] = Field(max_length=50)
    delivered: list[str] = Field(max_length=50)
    start: list[str] = Field(max_length=50)
    finish: list[str] = Field(max_length=50)


class DriverWordsOut(DriverWords):
    # The original lists, for "Back to the original words"
    defaults: DriverWords


class DriverMessageOut(BaseModel):
    """A driver's message to the bot and what it did (Plan 5)."""

    id: int
    driver: str
    # The order it was about, and its number as dispatchers see it; None for "start", "finish" and other messages
    order_id: int | None
    order_number: int | None
    text: str
    # minutes, arrived, delivered, start, finish, other
    meaning: str
    # What happened ("arrives in ~10 min (~14:35)", "delivered", "on duty"), or why not ("already delivered")
    result: str | None
    created_at: datetime


class DriverSetupInfo(BaseModel):
    traccar_client_url: str
    # Reporting settings for the set-up QR code / link
    traccar_interval_seconds: int
    traccar_distance_meters: int
    traccar_accuracy: str
    bot_username: str | None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sku: str | None
    price: float
    is_active: bool
    created_at: datetime


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=64)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=64)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    is_active: bool | None = None


class RecentAddressOut(BaseModel):
    """A drop-off address suggested for the customer's next order."""

    address: str
    label: str | None  # from an import ("Home"), if any
    # Newest order that went there; None for an imported address no order has used yet
    last_used_at: datetime | None
    latitude: float | None
    longitude: float | None


class CustomerPhoneIn(BaseModel):
    label: str | None = Field(default=None, max_length=50)
    phone: str = Field(min_length=1, max_length=32)


class CustomerPhoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str | None
    phone: str


class CustomerIn(BaseModel):
    """Adding or editing a customer sends everything, including the full list of phones. Addresses
    aren't edited here: they come from the customer's orders."""

    name: str = Field(min_length=1, max_length=200)
    # The first one is the main number
    phones: list[CustomerPhoneIn] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)
    is_active: bool = True
    # Meeting points (Plan 4): drivers can't ask this customer to meet at a spot. Left out: unchanged
    always_to_door: bool | None = None


class CustomerOut(BaseModel):
    id: int
    name: str
    phones: list[CustomerPhoneOut]
    notes: str | None
    is_active: bool
    always_to_door: bool
    # How often they declined a meeting point; at 2, "always to the door" switches on by itself
    meeting_declines: int
    # Where their orders went, newest first (then imported addresses), at most 5
    recent_addresses: list[RecentAddressOut]
    order_count: int
    last_order_at: datetime | None
    created_at: datetime


class CustomerRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None  # the main number


class ImportProblem(BaseModel):
    row: int  # spreadsheet row (header = 1) or contact number in a .vcf
    where: str  # "Row 4", "Contact 3 (Anna Smith)"
    message: str


class ImportResult(BaseModel):
    dry_run: bool
    customers_created: int
    customers_updated: int
    phones_added: int
    addresses_added: int
    problems: list[ImportProblem]


class DriverRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    # No Telegram: the dispatcher records for this driver
    recorded_by_dispatcher: bool = False


class OrderItemIn(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=10000)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    product_name: str
    unit_price: float
    quantity: int
    line_total: float


class OrderUpdate(BaseModel):
    """The editable details; an edit sends all of them."""

    # Required, except when editing an older order that never had one
    customer_id: int | None = None
    # Any address: drop-offs change from order to order
    dropoff_address: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)
    items: list[OrderItemIn] = Field(default_factory=list, max_length=50)
    # Customer in a rush / must have it by this time. A time without a zone ("2026-10-23T14:00") is in APP_TIMEZONE.
    # Left out: a new order gets placed + MAX_WAIT_MINUTES, an edit keeps the current values; null removes the time
    is_urgent: bool = False
    deliver_by: datetime | None = None
    # Overrides the location Google finds for the address; used for "nearest driver"
    dropoff_lat: float | None = Field(default=None, ge=-90, le=90)
    dropoff_lng: float | None = Field(default=None, ge=-180, le=180)


class LocateIn(BaseModel):
    customer_id: int | None = None
    address: str = Field(min_length=1, max_length=500)


class LocateOut(BaseModel):
    """Where a typed drop-off address is, checked before saving so a typo can be fixed first."""

    # False when Google isn't set up: nothing could be looked up, so there's nothing to warn about
    checked: bool
    latitude: float | None
    longitude: float | None
    # What Google found, and how precisely: "exact" (a house), "street" (only the street), "place" (found by name);
    # None for a customer's earlier drop-off or nothing found
    found_address: str | None = None
    precision: str | None = None


class OrderCreate(OrderUpdate):
    customer_id: int
    # Only this driver is offered the order; None = automatic
    target_driver_id: int | None = None


class ChangeRequestOut(BaseModel):
    id: int
    driver: str
    text: str
    # New quantities the driver entered, already applied to the order (the dispatcher just acknowledges)
    applied: bool
    created_at: datetime


class MeetingOut(BaseModel):
    """A driver's request to meet the customer at a spot instead of the door (Plan 4)."""

    status: str
    # "I'm parked here": the driver is already waiting, so call the customer now
    parked_here: bool
    spot_name: str | None
    spot_lat: float
    spot_lng: float
    # The customer's walk from the door, and what it saves the driver (a range; None when not known)
    walk_meters: int | None
    walk_seconds: int | None
    saving_min_seconds: int | None
    saving_max_seconds: int | None
    # The customer should hear about it by then; None for "I'm parked here" or when not known
    send_by: datetime | None
    created_at: datetime
    # When the dispatcher sent it to the customer, and when it was settled (agreed, declined, expired)
    asked_at: datetime | None
    resolved_at: datetime | None
    # The ready-made message for the customer, and how often they declined before
    customer_message: str
    customer_declines: int


class DriverPick(BaseModel):
    driver_id: int


class MinutesIn(BaseModel):
    minutes: int = Field(ge=1, le=180)


class OrderCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class OrderOut(BaseModel):
    id: int
    # The day's number dispatchers and drivers see ("#3"), the order's day, and the full number ("2026-11-03-003")
    number: int
    order_day: date
    ref: str
    status: str
    customer: CustomerRef | None
    is_urgent: bool
    deliver_by: datetime | None
    # deliver_by is the automatic waiting limit rather than a time someone chose
    deliver_by_auto: bool
    # Route planning, while a driver has the order: stop number and planned arrival range (see Plan 1)
    route_position: int | None
    # How many stops the driver's route has ("stop 3 of 5")
    route_stops: int | None
    planned_arrival_at: datetime | None
    planned_arrival_latest: datetime | None
    eta_estimated: bool
    # "Ask driver for ETA": when asked; the driver's answer (arrival, "or later" for 10+ min), and when given
    eta_asked_at: datetime | None
    driver_eta_at: datetime | None
    driver_eta_answered_at: datetime | None
    driver_eta_or_later: bool
    dropoff_address: str
    notes: str | None
    dropoff_lat: float | None
    dropoff_lng: float | None
    items: list[OrderItemOut]
    total: float
    driver: DriverRef | None
    target_driver: DriverRef | None
    # While PENDING: the driver currently deciding, or "no driver" once nobody is left to ask
    offered_to: DriverRef | None
    offer_expires_at: datetime | None
    no_driver_available: bool
    # While EN_ROUTE, once the driver tapped "arriving in N min": when they said it and when they'll arrive
    eta_reported_at: datetime | None
    eta_at: datetime | None
    # Changes the driver reported that no dispatcher has handled yet
    open_changes: list[ChangeRequestOut]
    # The driver's meeting point request, while the order is being delivered
    meeting: MeetingOut | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class AppSettingsOut(BaseModel):
    route_optimization: bool
    max_wait_minutes: int
    # Times are shown and entered in this zone (APP_TIMEZONE), whatever the dispatcher's computer is set to
    timezone: str
    # Google Route Matrix pairs requested today (UTC day) and the daily limit, to watch costs
    routes_pairs_today: int
    routes_daily_limit: int
    # Live driver map; empty until set up (the map page then says what's missing)
    maps_browser_key: str
    maps_map_id: str
    # Address suggestions are limited to this country (ADDRESS_COUNTRY, e.g. "GB")
    address_country: str
    # The delivery area for address suggestions and the map picker: centre and radius, or None (the whole country)
    address_area: list[float] | None
    # Address suggestions only in these towns (ADDRESS_TOWNS); empty: any town in the area
    address_towns: list[str]
    # Dispatchers' own reply to customers; placeholders {time} {latest} {minutes} {name} {order}
    reply_template: str | None


class ReplyTemplateIn(BaseModel):
    text: str | None = Field(default=None, max_length=500)


class TimelineItem(BaseModel):
    at: datetime
    kind: str  # "status" | "offer" | "change"
    text: str


class OrderDetail(OrderOut):
    timeline: list[TimelineItem]
