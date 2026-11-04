"""What a driver's message to the bot means (Plan 5): drivers reply to an order's message the way they always have,
with the minutes until they get there ("10"), "here" when they arrive and "done" when it's delivered, and send
"start" / "finish" to go on and off duty. The whole message counts, ignoring capitals, spaces and full stops. The
word lists are edited on the website (administrators); numbers always mean minutes."""

import json
import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

# 1–180 minutes: "10", "10 min", "10mins", "in 10", "10m"
MAX_MINUTES = 180
_MINUTES = re.compile(r"^(?:in\s*)?(\d{1,3})\s*(?:m|min|mins|minute|minutes)?$")
# The lists the website edits, in this order, and the original words ("Back to the original words")
KINDS = ("arrived", "delivered", "start", "finish")
LIST_NAMES = {"arrived": "Arrived", "delivered": "Delivered", "start": "On duty", "finish": "Off duty"}
DEFAULT_WORDS = {
    "arrived": ["here", "arrived", "outside"],
    "delivered": ["done", "delivered", "dn", "✅", "👍"],
    "start": ["start", "on"],
    "finish": ["finish", "off"],
}
WORDS_SETTING = "driver_reply_words"
MAX_WORD_LENGTH = 40


def plain(text: str) -> str:
    """The message as compared with the words: lower case, spaces and full stops evened out, emoji variants and skin
    tones dropped (👍🏽 = 👍)."""
    text = re.sub(r"[\s.!]+", " ", text.lower()).strip()
    return re.sub("[\ufe0f\U0001f3fb-\U0001f3ff]", "", text)


async def load_words(session: AsyncSession) -> dict[str, list[str]]:
    setting = await session.get(AppSetting, WORDS_SETTING)
    return json.loads(setting.value) if setting else DEFAULT_WORDS


def tidy_words(words: dict[str, list[str]]) -> dict[str, list[str]]:
    """Each list as the bot compares it (plain), without blanks or repeats, in the order typed."""
    return {kind: list(dict.fromkeys(w for w in map(plain, words.get(kind, [])) if w)) for kind in KINDS}


def words_problem(words: dict[str, list[str]]) -> str | None:
    """Why these lists can't be used (for the website), or None."""
    seen: dict[str, str] = {}
    for kind in KINDS:
        if not words[kind]:
            return f"{LIST_NAMES[kind]} needs at least one word."
        for word in words[kind]:
            if len(word) > MAX_WORD_LENGTH:
                return f"“{word}” is too long (at most {MAX_WORD_LENGTH} characters)."
            if _MINUTES.match(word):
                return f"“{word}” can't be a word: numbers always mean minutes."
            if word in seen:
                return f"“{word}” is in both {LIST_NAMES[seen[word]]} and {LIST_NAMES[kind]}: it can only mean one."
            seen[word] = kind
    return None


@dataclass
class Meaning:
    # "minutes", "arrived", "delivered", "start", "finish", or "other" (anything else)
    kind: str
    minutes: int | None = None


def understand(text: str, words: dict[str, list[str]] = DEFAULT_WORDS) -> Meaning:
    said = plain(text)
    if (m := _MINUTES.match(said)) and 1 <= int(m[1]) <= MAX_MINUTES:
        return Meaning("minutes", int(m[1]))
    for kind in KINDS:
        if said in words.get(kind, []):
            return Meaning(kind)
    return Meaning("other")
