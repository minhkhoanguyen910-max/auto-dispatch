import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Driver


def new_link_code() -> str:
    # URL-safe characters only, as Telegram requires for /start parameters
    return secrets.token_urlsafe(9)


def new_traccar_id() -> str:
    """Identifier for the Traccar Client app: short and easy to type on a phone."""
    return f"{secrets.randbelow(10**6):06d}"


async def link_telegram(session: AsyncSession, code: str, telegram_user_id: int) -> Driver | None:
    """Attach a Telegram account to the driver who owns this one-time code.

    Returns the driver, or None if the code is unknown or already used. If this Telegram
    account was linked to another driver before, that old link is removed.
    """
    driver = await session.scalar(select(Driver).where(Driver.link_code == code))
    if driver is None:
        return None
    previous = await session.scalar(
        select(Driver).where(Driver.telegram_user_id == telegram_user_id, Driver.id != driver.id)
    )
    if previous is not None:
        previous.telegram_user_id = None
        await session.flush()
    driver.telegram_user_id = telegram_user_id
    driver.link_code = None
    await session.commit()
    return driver
