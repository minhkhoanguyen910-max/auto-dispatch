import httpx

from app.config import settings

_bot_username: str | None = None


async def bot_username() -> str | None:
    """The bot's @username (without @), fetched once from Telegram. None if no token is set."""
    global _bot_username
    if _bot_username is None and settings.telegram_bot_token:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")
            resp.raise_for_status()
            _bot_username = resp.json()["result"]["username"]
    return _bot_username
