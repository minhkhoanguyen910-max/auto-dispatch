"""Driver Telegram bot and the dispatch worker. Run with: python -m app.bot"""

import asyncio
import logging
import sys
import time
from dataclasses import asdict
from decimal import Decimal

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ForceReply, KeyboardButton, Message, ReactionTypeEmoji, ReplyKeyboardMarkup, ReplyKeyboardRemove
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Driver, Order, OrderStatus
from app.services import dispatch, meeting, planning
from app.services.drivers import link_telegram
from app.services.notifier import TelegramNotifier
from app.services.roads import RoutingUnavailable
from app.telegram_ui import (
    ACCEPT,
    ASK_ETA,
    CHANGE,
    CHANGE_TEXT,
    DECLINE,
    ETA,
    MAX_QUANTITY,
    MEET,
    MEET_CANCEL,
    MEET_SEND,
    NOT_HERE,
    PARK,
    QTY_CANCEL,
    QTY_SEND,
    STATUS,
    USUAL,
    meeting_check,
    meeting_prompt,
    usual_spot_button,
    quantity_question,
    quantity_summary,
)

log = logging.getLogger("bot")
# One update at a time per driver, in order: e.g. two quick taps or typed numbers never overlap
dp = Dispatcher(events_isolation=SimpleEventIsolation())
DISPATCH_INTERVAL_SECONDS = 2


class ReportChange(StatesGroup):
    """Driver tapped "Report a change". `quantity`: the bot asks each product's new quantity and the
    driver types numbers. `text`: their next message is a written report."""

    quantity = State()
    text = State()


class MeetingPoint(StatesGroup):
    """Driver tapped "Meet the customer instead" (or "I'm parked here"). `spot`: their next message is the spot's
    location. `confirm`: the spot is checked; waiting for Ask the customer / Cancel."""

    spot = State()
    confirm = State()


@dp.message(CommandStart(deep_link=True))
async def link_driver(message: Message, command: CommandObject) -> None:
    """Driver opened t.me/<bot>?start=<link_code>: attach this chat to their record."""
    async with SessionLocal() as session:
        driver = await link_telegram(session, command.args, message.from_user.id)
    if driver is None:
        await message.answer("This link is invalid or was already used. Ask your dispatcher for a new one.")
        return
    await message.answer(f"Hi {driver.name}, you're connected. New orders will be sent here.")


@dp.message(CommandStart())
async def start(message: Message) -> None:
    async with SessionLocal() as session:
        driver = await session.scalar(select(Driver).where(Driver.telegram_user_id == message.from_user.id))
    if driver:
        await message.answer(f"Hi {driver.name}, you're connected. New orders will be sent here.\n\n/orders - your active orders")
    else:
        await message.answer("This bot is for drivers. Open the link your dispatcher sent you to connect.")


@dp.message(Command("orders"))
async def my_orders(message: Message, bot: Bot) -> None:
    async with SessionLocal() as session:
        driver = await session.scalar(select(Driver).where(Driver.telegram_user_id == message.from_user.id))
        if driver is None:
            await message.answer("This bot is for drivers. Open the link your dispatcher sent you to connect.")
            return
        await dispatch.send_delivery_list(session, driver.id, TelegramNotifier(bot))


@dp.callback_query(F.data.startswith(f"{ACCEPT}:") | F.data.startswith(f"{DECLINE}:"))
async def answer_offer(callback: CallbackQuery, bot: Bot) -> None:
    action, offer_id = callback.data.split(":")
    handler = dispatch.accept_offer if action == ACCEPT else dispatch.decline_offer
    async with SessionLocal() as session:
        reply = await handler(session, int(offer_id), callback.from_user.id, TelegramNotifier(bot))
    await callback.answer(reply.text)


@dp.callback_query(F.data.startswith(f"{STATUS}:"))
async def set_status(callback: CallbackQuery, bot: Bot) -> None:
    _, order_id, status = callback.data.split(":")
    async with SessionLocal() as session:
        reply = await dispatch.driver_set_status(
            session, int(order_id), OrderStatus(status), callback.from_user.id, TelegramNotifier(bot)
        )
    await callback.answer(reply.text)


@dp.callback_query(F.data.startswith(f"{ETA}:"))
async def report_eta(callback: CallbackQuery, bot: Bot) -> None:
    _, order_id, minutes = callback.data.split(":")
    async with SessionLocal() as session:
        reply = await dispatch.driver_report_eta(
            session, int(order_id), int(minutes), callback.from_user.id, TelegramNotifier(bot)
        )
    await callback.answer(reply.text)


@dp.callback_query(F.data.startswith(f"{ASK_ETA}:"))
async def answer_eta_question(callback: CallbackQuery) -> None:
    """Driver's answer to the dispatcher's "when will you reach #52?" (3 / 5 / 7 / 10+ min)."""
    _, order_id, minutes = callback.data.split(":")
    async with SessionLocal() as session:
        reply = await dispatch.driver_answer_eta(session, int(order_id), int(minutes), callback.from_user.id)
    await callback.answer(reply.text)
    if reply.text.startswith("Thanks") and callback.message:
        # Keep the question, now with the answer instead of the buttons
        await callback.message.edit_text(f"{callback.message.html_text}\n\n✅ {reply.text}", parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith(f"{CHANGE}:"))
async def start_quantities(callback: CallbackQuery, state: FSMContext) -> None:
    """ "Report a change": ask each product's new quantity in turn; the driver types the numbers."""
    order_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session:
        refused = await dispatch.driver_cant_report_change(session, order_id, callback.from_user.id)
        if refused is not None:
            await callback.answer(refused.text, show_alert=True)
            return
        rows = await dispatch.quantity_rows(session, order_id)
    await callback.answer()
    # Progress lives in the bot's memory (plain values); after a bot restart the driver starts again
    await state.clear()
    await state.set_state(ReportChange.quantity)
    await state.update_data(
        order_id=order_id,
        rows=[[r.product_id, r.name, str(r.price), r.quantity] for r in rows],
        typed=[],
        summary_id=None,
    )
    await _ask_quantity(callback.message, order_id, rows[0].name, rows[0].quantity, 1, len(rows))


async def _ask_quantity(message: Message, order_id: int, name: str, ordered: int, n: int, count: int) -> None:
    text, keyboard = quantity_question(order_id, await _number(order_id), name, ordered, n, count)
    question = await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await _track(order_id, question.chat.id, [question.message_id])


async def _summary(callback: CallbackQuery, state: FSMContext, order_id: int) -> dict | None:
    """The driver's quantities, if this Send/Cancel belongs to their current summary."""
    data = await state.get_data()
    if await state.get_state() == ReportChange.quantity and data.get("order_id") == order_id and data.get("summary_id") == callback.message.message_id:
        return data
    await callback.answer("This has expired. Tap ✏️ Report a change again.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)
    return None


@dp.callback_query(F.data.startswith(f"{QTY_SEND}:"))
async def send_quantities(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[1])
    if (data := await _summary(callback, state, order_id)) is None:
        return
    await state.clear()  # before the slow part, so a second tap on Send finds nothing to send
    quantities = {pid: qty for (pid, *_), qty in zip(data["rows"], data["typed"])}
    async with SessionLocal() as session:
        applied, reply = await dispatch.driver_change_quantities(session, order_id, callback.from_user.id, quantities, TelegramNotifier(bot))
    await callback.answer("Updated." if applied else reply.text, show_alert=not applied)
    await callback.message.edit_text(reply.text if applied else f"Order #{await _number(order_id)}: not sent. {reply.text}", parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith(f"{QTY_CANCEL}:"))
async def cancel_quantities(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.split(":")[1])
    if await _summary(callback, state, order_id) is None:
        return
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(f"Order #{await _number(order_id)}: no change sent.")


@dp.callback_query(F.data.startswith(f"{CHANGE_TEXT}:"))
async def ask_for_change(callback: CallbackQuery, state: FSMContext) -> None:
    """From the first quantity question: something other than quantities changed, so write it instead."""
    order_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session:
        refused = await dispatch.driver_cant_report_change(session, order_id, callback.from_user.id)
    if refused is not None:
        await callback.answer(refused.text, show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await state.set_state(ReportChange.text)
    await state.update_data(order_id=order_id)
    await callback.answer()
    prompt = await callback.message.answer(
        f"✏️ What changed in order #{await _number(order_id)}? Write it in one message, "
        "e.g. \"Customer wants 3 instead of 2\".\n\n/cancel to stop.",
        reply_markup=ForceReply(input_field_placeholder="What changed?"),
    )
    await _track(order_id, prompt.chat.id, [prompt.message_id])


@dp.callback_query(F.data.startswith(f"{MEET}:") | F.data.startswith(f"{PARK}:"))
async def start_meeting(callback: CallbackQuery, state: FSMContext) -> None:
    """ "Meet the customer instead": the driver sends the spot. "I'm parked here": where the phone is (GPS, else
    the driver sends their location)."""
    action, order_id = callback.data.split(":")
    order_id, parked = int(order_id), action == PARK
    async with SessionLocal() as session:
        refused = await dispatch.driver_cant_ask_meeting(session, order_id, callback.from_user.id, parked)
        here = await dispatch.driver_parked_position(session, callback.from_user.id) if parked and refused is None else None
        usual = await dispatch.driver_usual_spot(session, order_id, callback.from_user.id) if not parked and refused is None else None
    if refused is not None:
        await callback.answer(refused.text, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(MeetingPoint.spot)
    await state.update_data(order_id=order_id, parked=parked)
    if here is not None:
        await _check_spot(callback.message, state, callback.from_user.id, here)
        return
    if parked:
        keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📍 Send my location", request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
    else:
        keyboard = usual_spot_button(order_id, usual.id, usual.name) if usual else None
    prompt = await callback.message.answer(meeting_prompt(await _number(order_id), parked), parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await _track(order_id, prompt.chat.id, [prompt.message_id])


@dp.callback_query(F.data.startswith(f"{USUAL}:"))
async def use_usual_spot(callback: CallbackQuery, state: FSMContext) -> None:
    """ "Same place as last time": check that spot as if the driver had sent it."""
    _, order_id, spot_id = callback.data.split(":")
    if await state.get_state() != MeetingPoint.spot or (await state.get_data()).get("order_id") != int(order_id):
        await callback.answer("This has expired. Tap 📍 Meet the customer instead again.", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    async with SessionLocal() as session:
        spot = await meeting.spot_location(session, int(spot_id))
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _check_spot(callback.message, state, callback.from_user.id, spot)


@dp.message(MeetingPoint.spot, F.location)
async def got_spot(message: Message, state: FSMContext) -> None:
    await _track((await state.get_data())["order_id"], message.chat.id, [message.message_id])
    await _check_spot(message, state, message.from_user.id, (message.location.latitude, message.location.longitude))


@dp.message(MeetingPoint.spot)
async def spot_not_location(message: Message, state: FSMContext) -> None:
    """Not a location: maybe a typed position or a Google Maps link (Telegram on a computer can't send locations)."""
    order_id = (await state.get_data())["order_id"]
    spot = await meeting.read_position(message.text or message.caption or "")
    if spot is not None:
        await _track(order_id, message.chat.id, [message.message_id])
        await _check_spot(message, state, message.from_user.id, spot)
        return
    answer = await message.answer(
        "Please send the spot as a location (📎 → Location), type its position like 50.8562, 0.5810, "
        "or paste a Google Maps link. /cancel to stop."
    )
    await _track(order_id, message.chat.id, [message.message_id, answer.message_id])


async def _check_spot(message: Message, state: FSMContext, telegram_user_id: int, spot: tuple[float, float]) -> None:
    """Measure the spot and show it to the driver with Ask the customer / Cancel."""
    data = await state.get_data()
    order_id, parked = data["order_id"], data["parked"]
    checking = await message.answer("⏳ Checking the spot…", reply_markup=ReplyKeyboardRemove())
    try:
        async with SessionLocal() as session:
            result = await dispatch.driver_check_meeting(session, order_id, telegram_user_id, spot, parked)
    except meeting.SpotProblem as problem:
        result = f"{problem}\n\nSend another spot, or /cancel."
    except RoutingUnavailable:
        await state.clear()
        result = "The map isn't available right now, so the spot can't be checked. Please try again in a few minutes."
    try:
        await checking.delete()
    except TelegramBadRequest:
        pass
    if isinstance(result, dispatch.Reply):
        await state.clear()
        result = result.text
    if isinstance(result, str):
        answer = await message.answer(result)
        await _track(order_id, answer.chat.id, [answer.message_id])
        return
    text, keyboard = meeting_check(order_id, await _number(order_id), result)
    summary = await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await _track(order_id, summary.chat.id, [summary.message_id])
    await state.set_state(MeetingPoint.confirm)
    await state.update_data(checked=asdict(result), summary_id=summary.message_id)


async def _checked_spot(callback: CallbackQuery, state: FSMContext, order_id: int) -> dict | None:
    """The checked spot, if this Ask / Cancel belongs to the driver's latest one."""
    data = await state.get_data()
    if await state.get_state() == MeetingPoint.confirm and data.get("order_id") == order_id and data.get("summary_id") == callback.message.message_id:
        return data
    await callback.answer("This has expired. Tap 📍 Meet the customer instead again.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)
    return None


@dp.callback_query(F.data.startswith(f"{MEET_SEND}:"))
async def send_meeting(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[1])
    if (data := await _checked_spot(callback, state, order_id)) is None:
        return
    await state.clear()  # before the slow part, so a second tap finds nothing to send
    async with SessionLocal() as session:
        reply = await dispatch.driver_request_meeting(
            session, order_id, callback.from_user.id, meeting.Check(**data["checked"]), TelegramNotifier(bot)
        )
    await callback.answer()
    await callback.message.edit_text(f"{callback.message.html_text}\n\n{reply.text}", parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith(f"{MEET_CANCEL}:"))
async def cancel_meeting(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.split(":")[1])
    if await _checked_spot(callback, state, order_id) is None:
        return
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(f"Order #{await _number(order_id)}: nothing sent, the stop stays the door.")


@dp.callback_query(F.data.startswith(f"{NOT_HERE}:"))
async def customer_not_here(callback: CallbackQuery, bot: Bot) -> None:
    """At the meeting point the customer didn't come: the stop goes back to the door."""
    order_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session:
        reply = await dispatch.driver_customer_not_here(session, order_id, callback.from_user.id, TelegramNotifier(bot))
    await callback.answer(reply.text, show_alert=True)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


async def _number(order_id: int) -> int:
    """The order's number that day, as drivers see it ("#3")."""
    async with SessionLocal() as session:
        return await session.scalar(select(Order.shown_number).where(Order.id == order_id))


async def _track(order_id: int, chat_id: int, message_ids: list[int]) -> None:
    """These messages are about the order: delete them along with its others once it's finished."""
    async with SessionLocal() as session:
        await dispatch.track_messages(session, order_id, chat_id, message_ids)


@dp.message(Command("cancel"))
async def cancel_report(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Nothing to cancel.")
        return
    order_id = (await state.get_data())["order_id"]
    await state.clear()
    # Also takes away "Send my location" if the bot was asking where the driver is parked
    answer = await message.answer("OK, nothing was sent.", reply_markup=ReplyKeyboardRemove())
    await _track(order_id, message.chat.id, [message.message_id, answer.message_id])


@dp.message(ReportChange.text, F.text | F.caption)
async def send_change(message: Message, state: FSMContext) -> None:
    order_id = (await state.get_data())["order_id"]
    await state.clear()
    async with SessionLocal() as session:
        reply = await dispatch.driver_report_change(session, order_id, message.from_user.id, message.text or message.caption)
    answer = await message.answer(reply.text)
    await _track(order_id, message.chat.id, [message.message_id, answer.message_id])


@dp.message(ReportChange.text)
async def change_not_text(message: Message, state: FSMContext) -> None:
    answer = await message.answer("Please write the change as a text message, or /cancel.")
    await _track((await state.get_data())["order_id"], message.chat.id, [message.message_id, answer.message_id])


@dp.message(ReportChange.quantity)
async def type_quantity(message: Message, state: FSMContext) -> None:
    """The driver typed a quantity: ask the next product, or show the summary after the last."""
    data = await state.get_data()
    order_id, rows, typed = data["order_id"], data["rows"], data["typed"]
    if data["summary_id"] is not None:
        answer = await message.answer("Tap ✅ Send or ✖ Cancel above, or /cancel.")
        await _track(order_id, message.chat.id, [message.message_id, answer.message_id])
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) > MAX_QUANTITY:
        answer = await message.answer(f"Please type a number from 0 to {MAX_QUANTITY}, or /cancel.")
        await _track(order_id, message.chat.id, [message.message_id, answer.message_id])
        return

    typed = [*typed, int(raw)]
    await state.update_data(typed=typed)
    await _track(order_id, message.chat.id, [message.message_id])
    if len(typed) < len(rows):
        _, name, _, ordered = rows[len(typed)]
        await _ask_quantity(message, order_id, name, ordered, len(typed) + 1, len(rows))
        return

    text, keyboard = quantity_summary(order_id, await _number(order_id), [(name, Decimal(price), ordered) for _, name, price, ordered in rows], typed)
    summary = await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await _track(order_id, summary.chat.id, [summary.message_id])
    if keyboard is None:
        await state.clear()  # nothing to send
    else:
        await state.update_data(summary_id=summary.message_id)


def _message_text(message: Message) -> str:
    """What the driver sent, as text: photos, voice messages and the like by what they are (shown in the web)."""
    if message.text or message.caption:
        return message.text or message.caption
    for kind, label in (("photo", "📷 Photo"), ("voice", "🎤 Voice message"), ("location", "📍 Location"), ("document", "📎 File"), ("video", "🎬 Video")):
        if getattr(message, kind):
            return label
    if message.sticker:
        return message.sticker.emoji or "Sticker"
    return "Message"


# Registered last, and only outside the bot's questions (new quantities, a meeting spot): those answers go to them
@dp.message(StateFilter(None))
async def driver_wrote(message: Message, bot: Bot) -> None:
    """Plan 5: drivers reply to an order's message ("10", "here", "done", or anything else) and send "start" / "finish"."""
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None
    async with SessionLocal() as session:
        answer = await dispatch.driver_message(
            session, message.from_user.id, message.chat.id, message.message_id, _message_text(message), reply_to, TelegramNotifier(bot)
        )
    if answer.delete:
        # "done": the card now says Delivered, and the order's other messages are already gone
        try:
            await message.delete()
        except TelegramBadRequest as err:
            log.info("Couldn't delete driver %s's message: %s", message.from_user.id, err)
        return
    ids = [message.message_id]
    if answer.reaction:
        try:
            await bot.set_message_reaction(message.chat.id, message.message_id, [ReactionTypeEmoji(emoji=answer.reaction)])
        except TelegramBadRequest as err:
            log.info("Couldn't react to driver %s's message: %s", message.from_user.id, err)
    if answer.text:
        ids.append((await message.reply(answer.text, parse_mode=ParseMode.HTML)).message_id)
    # About an order still being delivered: a reply to these finds it too, and they're tidied away with it
    for order_id in [answer.order_id] if answer.order_id is not None else answer.tie_to or []:
        await _track(order_id, message.chat.id, ids)


async def dispatch_worker(bot: Bot) -> None:
    notifier = TelegramNotifier(bot)
    next_drift_check = 0.0
    while True:
        try:
            async with SessionLocal() as session:
                await dispatch.dispatch_tick(session, notifier)
                # Route planning: drivers running ahead of or behind plan get new ETAs (Plan 1 step 4)
                if time.monotonic() >= next_drift_check:
                    next_drift_check = time.monotonic() + planning.DRIFT_CHECK_EVERY.total_seconds()
                    await dispatch.check_route_drift(session, notifier)
        except Exception:
            log.exception("Dispatch tick failed")
        await asyncio.sleep(DISPATCH_INTERVAL_SECONDS)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if not settings.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN is not set; bot disabled")
        return
    bot = Bot(settings.telegram_bot_token)
    worker = asyncio.create_task(dispatch_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
