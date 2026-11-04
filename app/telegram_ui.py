"""What drivers see in Telegram: message text and buttons for an order, by its current state."""

from decimal import Decimal
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.models import MeetingStatus, OfferResult, Order, OrderOffer, OrderStatus
from app.services.eta import format_local
from app.services.meeting import CUSTOMER_WAIT, MAX_WALK_M, Check, navigation_link, point_of

# Callback data. Telegram allows at most 64 bytes.
ACCEPT, DECLINE, STATUS, ETA, CHANGE = "acc", "dec", "st", "eta", "chg"
# The "new quantities" dialog (the driver types each number): send, cancel, write a report instead
QTY_SEND, QTY_CANCEL, CHANGE_TEXT = "qs", "qc", "ct"
MAX_QUANTITY = 999
# Meeting points (Plan 4): meet the customer at a spot, "I'm parked here" after Arrived; send / cancel the checked spot
MEET, PARK, MEET_SEND, MEET_CANCEL = "mt", "pk", "ms", "mc"
# At an agreed spot, the customer didn't come: go to the door
NOT_HERE = "nh"
# "Same place as last time": the spot that worked before for this customer and destination
USUAL = "us"

# "Arriving in N min" choices shown while the driver is on the way
ETA_MINUTES = (2, 3, 5)
# Answers to the dispatcher's "when will you reach #52?" (the last one means "or later")
ASK_ETA, ASK_ETA_MINUTES = "aeta", (3, 5, 7, 10)

# Driver's next step from each status: (button label, new status)
NEXT_STEP = {
    OrderStatus.ACCEPTED: ("🚚 On my way", OrderStatus.EN_ROUTE),
    OrderStatus.EN_ROUTE: ("📍 Arrived", OrderStatus.AT_STOP),
    OrderStatus.AT_STOP: ("✅ Delivered", OrderStatus.COMPLETED),
}

STATUS_HEADER = {
    OrderStatus.ACCEPTED: "📦 Your order",
    OrderStatus.EN_ROUTE: "🚚 On the way",
    OrderStatus.AT_STOP: "📍 Arrived",
    OrderStatus.COMPLETED: "🎉 Delivered",
    OrderStatus.CANCELED: "❌ Canceled by dispatcher",
}


def money(value: Decimal) -> str:
    """12345 -> "12,345"; 12.5 -> "12.50"."""
    return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"


def urgency(order: Order) -> list[str]:
    """With route planning on: 🔴 Urgent, and a "deliver by" time the dispatcher chose (not the automatic
    1-hour limit, which every order has)."""
    if not settings.route_optimization:
        return []
    marks = ["🔴 <b>Urgent</b>"] if order.is_urgent else []
    if order.deliver_by and not order.deliver_by_auto:
        marks.append(f"⏰ <b>By {format_local(order.deliver_by)}</b>")
    return marks


def meeting_spot(order: Order) -> str | None:
    """With an agreed meeting point: "Queens Road (navigate)", the navigation link opening Google Maps."""
    m = order.meeting
    if m is None or m.status != MeetingStatus.AGREED:
        return None
    where = spot_name(m.spot.name)
    at = point_of(m.spot.location)
    return f'{where} (<a href="{navigation_link(*at)}">navigate</a>)' if at else where


def order_details(order: Order, distance_m: float | None = None) -> str:
    spot = meeting_spot(order)
    to = (
        [f"<b>Meet the customer at:</b> {spot}", f"<b>Their address:</b> {escape(order.dropoff_address)}"]
        if spot
        else [f"<b>To:</b> {escape(order.dropoff_address)}"]
    )
    lines = [*urgency(order), *to]
    if order.items:
        lines.append("<b>Items:</b>")
        lines += [f"• {i.quantity} × {escape(i.product_name)} — {money(i.line_total)}" for i in order.items]
        lines.append(f"<b>Total:</b> {money(order.total)}")
    if order.notes:
        lines.append(f"<b>Notes:</b> {escape(order.notes)}")
    if distance_m is not None:
        lines.append(f"<b>Distance to drop-off:</b> {distance_m / 1000:.1f} km")
    return "\n".join(lines)


def eta_question(order: Order) -> tuple[str, None]:
    """The dispatcher asks when the driver will reach this stop. No buttons (Plan 5): the driver replies with the
    minutes, as on the order's card. (Earlier questions' 3 / 5 / 7 / 10+ min buttons still work.)"""
    text = (
        f"⏱ When will you reach <b>#{order.shown_number}</b>?\n{escape(order.dropoff_address)}"
        "\n\n↩️ Reply with the minutes, e.g. <b>5</b>."
    )
    return text, None


def quantity_question(order_id: int, number: int, name: str, ordered: int, n: int, count: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """Ask the driver to type the new quantity of one product (product n of count). The first
    question also offers writing a report instead, for changes other than quantities."""
    text = (
        f"✏️ <b>Order #{number}: new quantities</b> ({n}/{count})\n\n"
        f"<b>{escape(name)}</b>: on the order {ordered}\n"
        "Type the quantity the customer takes now (0 = none).\n\n/cancel to stop."
    )
    if n > 1:
        return text, None
    write = InlineKeyboardButton(text="💬 Something else? Write a message", callback_data=f"{CHANGE_TEXT}:{order_id}")
    return text, InlineKeyboardMarkup(inline_keyboard=[[write]])


def quantity_summary(
    order_id: int, number: int, rows: list[tuple[str, Decimal, int]], typed: list[int]
) -> tuple[str, InlineKeyboardMarkup | None]:
    """What the driver typed, before sending: each product's quantity on the order → new, and the
    totals. `rows` are (name, price, quantity on the order). No buttons when there's nothing to send
    (nothing changed, or everything is 0, which is a cancellation)."""
    lines = [
        f"{escape(name)}: {ordered} → <b>{new}</b>" if new != ordered else f"{escape(name)}: {ordered} (no change)"
        for (name, _, ordered), new in zip(rows, typed)
    ]
    old_total = sum((price * ordered for _, price, ordered in rows), Decimal(0))
    new_total = sum((price * new for (_, price, _), new in zip(rows, typed)), Decimal(0))
    text = f"✏️ <b>Order #{number}: new quantities</b>\n\n" + "\n".join(lines)
    text += f"\n\n<b>Total:</b> {money(old_total)}" + (f" → <b>{money(new_total)}</b>" if new_total != old_total else "")
    if not any(typed):
        return text + "\n\n⚠️ Everything at 0 is a cancellation, not a change. Please call the dispatcher.", None
    if all(new == ordered for (_, _, ordered), new in zip(rows, typed)):
        return text + "\n\nNothing changed, so nothing was sent.", None
    buttons = [[
        InlineKeyboardButton(text="✅ Send", callback_data=f"{QTY_SEND}:{order_id}"),
        InlineKeyboardButton(text="✖ Cancel", callback_data=f"{QTY_CANCEL}:{order_id}"),
    ]]
    return text + "\n\nSend applies this to the order right away.", InlineKeyboardMarkup(inline_keyboard=buttons)


def minutes_range(low_seconds: int, high_seconds: int) -> str:
    """600, 840 -> "10–14 min" (never below 0)."""
    low, high = (max(0, round(s / 60)) for s in (low_seconds, high_seconds))
    return f"{low}–{high} min" if low != high else f"{low} min"


def spot_name(name: str | None) -> str:
    return escape(name) if name else "the spot"


# The driver's card while there's a meeting point request (Plan 4)
MEETING_LINE = {
    MeetingStatus.REQUESTED: "📍 You asked to meet the customer at {spot}. Waiting for the dispatcher; until then, the stop is the door.",
    MeetingStatus.ASKED: "📍 The dispatcher is asking the customer to meet you at {spot}. Until they agree, the stop is the door.",
    MeetingStatus.AGREED: "✅ The customer will meet you at {spot}.",
    MeetingStatus.DECLINED: "🚪 Deliver to the door as usual (the customer prefers the door).",
    MeetingStatus.EXPIRED: "🚪 Deliver to the door as usual (no answer from the customer in time).",
    MeetingStatus.NOT_THERE: "🚪 The customer wasn't at the spot: deliver to the door.",
}
PARKED_LINE = {
    MeetingStatus.REQUESTED: "📌 You're parked at {spot}. The dispatcher will call the customer to come out.",
    MeetingStatus.ASKED: "📌 The dispatcher called the customer to come out to {spot}.",
}


def not_here_question(order: Order) -> tuple[str, InlineKeyboardMarkup]:
    """CUSTOMER_WAIT after Arrived at the meeting point, not delivered yet."""
    m = order.meeting
    minutes = round(CUSTOMER_WAIT.total_seconds() / 60)
    if m.parked_here:
        text = f"⏱ <b>#{order.shown_number}: customer not out after {minutes} min?</b> Take it to the door: {escape(order.dropoff_address)}"
        label = "🚪 Customer not here: I'll take it to the door"
    else:
        text = f"⏱ <b>#{order.shown_number}: customer not at {spot_name(m.spot.name)} after {minutes} min?</b> Drive to the address: {escape(order.dropoff_address)}"
        label = "🚪 Customer not here: drive to the address"
    return text, InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"{NOT_HERE}:{order.id}")]])


def meeting_prompt(number: int, parked_here: bool) -> str:
    """Asks the driver for the spot's location."""
    if parked_here:
        return (
            f"📌 <b>Order #{number}: where are you parked?</b>\n\n"
            "Your phone's GPS position isn't recent, so tap <b>📍 Send my location</b> below "
            "(or type your position like 50.8562, 0.5810, or paste a Google Maps link).\n\n/cancel to stop."
        )
    return (
        f"📍 <b>Order #{number}: where should you meet the customer?</b>\n\n"
        f"Send the spot as a location: tap 📎 → <b>Location</b>, move the pin onto the street where you can stop, "
        f"and send it. The customer can walk at most {MAX_WALK_M} m.\n\n"
        "No Location option (Telegram on a computer)? Type the position like 50.8562, 0.5810, or paste a Google Maps "
        "link.\n\n/cancel to stop."
    )


def usual_spot_button(order_id: int, spot_id: int, name: str | None) -> InlineKeyboardMarkup:
    """Offered with the question where to meet, when this customer was met at a spot before at this destination."""
    label = f"🔁 Same place as last time: {name}" if name else "🔁 Same place as last time"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"{USUAL}:{order_id}:{spot_id}")]])


def meeting_check(order_id: int, number: int, measured: Check) -> tuple[str, InlineKeyboardMarkup]:
    """The measured spot, for the driver to confirm before the dispatcher asks the customer."""
    where = spot_name(measured.name)
    walk = f"🚶 The customer walks <b>{measured.walk_meters} m</b> (~{max(1, round(measured.walk_seconds / 60))} min)"
    if measured.parked_here:
        lines = [f"📌 <b>Order #{number}: ask the customer to come out to you at {where}?</b>", walk]
        if measured.walk_meters > MAX_WALK_M:
            lines.append("⚠️ That's a long walk for the customer. Ask anyway?")
        send = "✅ Ask the customer to come out"
    else:
        lines = [f"📍 <b>Order #{number}: meet the customer at {where}?</b>", walk]
        if measured.saving_seconds is None:
            lines.append("⏱ The time it saves isn't known (no GPS position from your phone).")
        elif measured.saving_seconds <= 0:
            lines.append("⚠️ This doesn't save you time compared with the door.")
        else:
            lines.append(f"⏱ Saves you about <b>{minutes_range(measured.saving_min_seconds, measured.saving_max_seconds)}</b>")
            if measured.small_saving:
                lines.append(f"⚠️ That's less than {settings.meeting_min_saving_minutes:g} min: maybe not worth asking the customer.")
        if measured.short_notice:
            lines.append(
                f"⚠️ Only about {max(0, round(measured.notice_seconds / 60))} min before you get there. "
                "The customer may not have time. Ask anyway?"
            )
        send = "✅ Ask the customer"
    buttons = [[
        InlineKeyboardButton(text=send, callback_data=f"{MEET_SEND}:{order_id}"),
        InlineKeyboardButton(text="✖ Cancel", callback_data=f"{MEET_CANCEL}:{order_id}"),
    ]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def _meeting_line(order: Order) -> str | None:
    m = order.meeting
    if m is None:
        return None
    line = (PARKED_LINE if m.parked_here else MEETING_LINE).get(m.status) or MEETING_LINE.get(m.status)
    return line.format(spot=spot_name(m.spot.name)) if line else None


def _meeting_button(order: Order) -> InlineKeyboardButton | None:
    """Ask for a meeting point: on any upcoming stop, or "I'm parked here" after Arrived; once per order."""
    if order.meeting is not None:
        return None
    if order.status == OrderStatus.AT_STOP:
        return InlineKeyboardButton(text="📌 I'm parked here, ask the customer to come out", callback_data=f"{PARK}:{order.id}")
    if order.status in (OrderStatus.ACCEPTED, OrderStatus.EN_ROUTE) and settings.meeting_points == "all":
        return InlineKeyboardButton(text="📍 Meet the customer instead", callback_data=f"{MEET}:{order.id}")
    return None


def delivery_list(stops: list[tuple[Order, float | None]], has_gps: bool) -> str:
    """All of a driver's current deliveries in driving order (nearest first, or the planned order with
    route planning on, with the approximate time at each stop), with the distance from the previous
    stop: where to go and what to bring."""
    if not stops:
        return "📋 <b>Your deliveries</b>\n\nNo deliveries right now."
    planned = settings.route_optimization
    order_word = " · in this order" if planned else " · nearest first" if has_gps else ""
    parts = [f"📋 <b>Your deliveries ({len(stops)}){order_word}</b>"]
    if not has_gps:
        parts.append(
            "⚠️ No GPS position from your phone yet, so the times are rough. Keep Traccar Client running."
            if planned
            else "⚠️ No GPS position from your phone yet, so these aren't sorted by distance. Keep Traccar Client running."
        )
    for n, (o, meters) in enumerate(stops, 1):
        if meters is not None:
            where = f" · {meters / 1000:.1f} km" if n == 1 else f" · {meters / 1000:.1f} km further"
        else:
            where = " · no map location" if has_gps else ""
        when = f" · ~{format_local(o.planned_arrival_at)}" if planned and o.planned_arrival_at else ""
        marks = "".join(f" · {m}" for m in urgency(o))
        spot = meeting_spot(o)
        stop = f"📍 Meet at {spot} · customer at {escape(o.dropoff_address)}" if spot else f"📍 {escape(o.dropoff_address)}"
        lines = [f"<b>{n}. Order #{o.shown_number}</b>{when}{where}{marks}", stop]
        if o.items:
            items = ", ".join(f"{i.quantity} × {escape(i.product_name)}" for i in o.items)
            lines.append(f"📦 {items} · {money(o.total)}")
        if o.notes:
            lines.append(f"📝 {escape(o.notes)}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def is_waiting(order: Order, sequence: list[int] | None) -> bool:
    """With route planning on: an accepted order that isn't the driver's next stop. It gets no "On my way"
    button; the driver follows the planned order."""
    return bool(
        settings.route_optimization
        and order.status == OrderStatus.ACCEPTED
        and sequence
        and order.id in sequence
        and sequence[0] != order.id
    )


def render(
    order: Order, offer: OrderOffer, distance_m: float | None = None, sequence: list[int] | None = None
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Text and buttons for the message a driver got when this order was offered to them. `sequence` is
    the driver's planned order of stops (route planning)."""
    title = f"order #{order.shown_number}"

    if offer.result is None and order.status == OrderStatus.PENDING:
        text = (
            f"📦 <b>New {title}</b>\n\n{order_details(order, distance_m)}\n\n"
            f"⏱ Please answer within {settings.offer_timeout_seconds} seconds."
        )
        buttons = [
            [
                InlineKeyboardButton(text="✅ Accept", callback_data=f"{ACCEPT}:{offer.id}"),
                InlineKeyboardButton(text="❌ Decline", callback_data=f"{DECLINE}:{offer.id}"),
            ],
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    if offer.result == OfferResult.DECLINED:
        return f"You declined {title}.", None
    if offer.result in (OfferResult.EXPIRED, None):
        if order.status == OrderStatus.CANCELED:
            return f"❌ {title.capitalize()} was canceled.", None
        return f"⏰ The offer for {title} expired.", None

    # Accepted: show progress and the next step
    text = f"<b>{STATUS_HEADER[order.status]} · {title}</b>\n\n{order_details(order)}"
    if order.status not in NEXT_STEP:
        return text, None
    if line := _meeting_line(order):
        text += f"\n\n{line}"
    meet = _meeting_button(order)
    if is_waiting(order, sequence):
        # Drivers can ask for a meeting point early, on any upcoming stop
        return text + "\n\n⏳ Not your next stop yet. Follow 📋 Your deliveries.", InlineKeyboardMarkup(inline_keyboard=[[meet]]) if meet else None
    # Plan 5: drivers reply to this message instead of tapping status buttons
    buttons = [[meet]] if meet else []
    if order.status == OrderStatus.EN_ROUTE and order.eta_at and order.eta_reported_at:
        text += f"\n\n⏱ The dispatcher knows you arrive ~{format_local(order.eta_at)}."
    # Changes are only known once the driver is at the customer
    if order.status == OrderStatus.AT_STOP:
        buttons.append([InlineKeyboardButton(text="✏️ Report a change", callback_data=f"{CHANGE}:{order.id}")])
        text += "\n\n↩️ Reply <b>done</b> to this message when it's delivered."
    else:
        text += "\n\n↩️ Reply to this message: <b>10</b> = you arrive in 10 min, <b>here</b> = arrived, <b>done</b> = delivered."
    return text, InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
