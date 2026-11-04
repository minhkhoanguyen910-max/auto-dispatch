"""Sends and updates drivers' Telegram messages. Dispatch logic only talks to this interface,
so it can be tested without Telegram."""

import logging
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

from app.models import Driver, Order, OrderOffer
from app.telegram_ui import render

log = logging.getLogger(__name__)
# Messages can hold a navigation link to a meeting point; a big map preview under every card would be noise
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class DeliveryError(Exception):
    """The driver can't be reached (blocked the bot, deleted their account, ...)."""


class Notifier(Protocol):
    async def send_offer(self, driver: Driver, order: Order, offer: OrderOffer, distance_m: float | None) -> int:
        """Send the offer message; returns its Telegram message id. Raises DeliveryError."""

    async def refresh(self, driver: Driver, order: Order, offer: OrderOffer) -> None:
        """Re-render an offer message to match the order's current state."""

    async def message(self, driver: Driver, text: str, keyboard: InlineKeyboardMarkup | None = None) -> int | None:
        """Send a separate message (it notifies the driver, unlike an edit), with buttons if given.
        Returns its id, or None if it couldn't be sent."""

    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        """Delete messages; ones that are gone or too old for Telegram (48 h) are skipped."""

    async def send_list(self, driver: Driver, text: str, replaces: int | None) -> int | None:
        """Send the driver's delivery list, deleting the previous one (message id `replaces`).
        Returns the new message id, or None if it couldn't be sent."""

    async def edit_list(self, driver: Driver, text: str, message_id: int) -> bool:
        """Change the driver's delivery list in place (no notification). False if that message is gone."""


class TelegramNotifier:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_offer(self, driver: Driver, order: Order, offer: OrderOffer, distance_m: float | None) -> int:
        text, keyboard = render(order, offer, distance_m)
        try:
            msg = await self.bot.send_message(driver.telegram_user_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW, reply_markup=keyboard)
        except (TelegramForbiddenError, TelegramBadRequest) as err:
            raise DeliveryError(str(err)) from err
        return msg.message_id

    async def refresh(self, driver: Driver, order: Order, offer: OrderOffer) -> None:
        if not (driver.telegram_user_id and offer.telegram_message_id):
            return
        text, keyboard = render(order, offer, sequence=driver.list_order_ids)
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=driver.telegram_user_id,
                message_id=offer.telegram_message_id,
                parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW,
                reply_markup=keyboard,
            )
        except (TelegramForbiddenError, TelegramBadRequest) as err:
            # e.g. "message is not modified", or the chat is gone; nothing more to do
            log.info("Couldn't update offer %s message: %s", offer.id, err)

    async def message(self, driver: Driver, text: str, keyboard: InlineKeyboardMarkup | None = None) -> int | None:
        if not driver.telegram_user_id:
            return None
        try:
            msg = await self.bot.send_message(driver.telegram_user_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW, reply_markup=keyboard)
        except (TelegramForbiddenError, TelegramBadRequest) as err:
            log.info("Couldn't message driver %s: %s", driver.id, err)
            return None
        return msg.message_id

    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        for start in range(0, len(message_ids), 100):  # Telegram's limit per call
            try:
                await self.bot.delete_messages(chat_id, message_ids[start : start + 100])
            except (TelegramForbiddenError, TelegramBadRequest) as err:
                log.info("Couldn't delete messages in chat %s: %s", chat_id, err)

    async def send_list(self, driver: Driver, text: str, replaces: int | None) -> int | None:
        if not driver.telegram_user_id:
            return None
        if replaces:
            try:
                await self.bot.delete_message(driver.telegram_user_id, replaces)
            except (TelegramForbiddenError, TelegramBadRequest) as err:
                # Already deleted, or older than Telegram allows deleting (48 h)
                log.info("Couldn't delete old delivery list of driver %s: %s", driver.id, err)
        try:
            msg = await self.bot.send_message(driver.telegram_user_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        except (TelegramForbiddenError, TelegramBadRequest) as err:
            log.info("Couldn't send delivery list to driver %s: %s", driver.id, err)
            return None
        return msg.message_id

    async def edit_list(self, driver: Driver, text: str, message_id: int) -> bool:
        if not driver.telegram_user_id:
            return False
        try:
            await self.bot.edit_message_text(
                text=text, chat_id=driver.telegram_user_id, message_id=message_id, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
            )
        except TelegramBadRequest as err:
            if "not modified" in str(err):
                return True
            log.info("Couldn't edit delivery list of driver %s: %s", driver.id, err)
            return False
        except TelegramForbiddenError as err:
            log.info("Couldn't edit delivery list of driver %s: %s", driver.id, err)
            return False
        return True


class NullNotifier:
    """Used when no bot token is configured."""

    async def send_offer(self, driver, order, offer, distance_m) -> int:
        raise DeliveryError("Telegram bot is not configured")

    async def refresh(self, driver, order, offer) -> None:
        pass

    async def message(self, driver, text, keyboard=None) -> int | None:
        return None

    async def delete(self, chat_id, message_ids) -> None:
        pass

    async def send_list(self, driver, text, replaces) -> int | None:
        return None

    async def edit_list(self, driver, text, message_id) -> bool:
        return False
