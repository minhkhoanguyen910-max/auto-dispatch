import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin
from app.config import settings
from app.db import get_session
from app.models import AppSetting, RoutesUsage
from app.services.geocoding import delivery_area
from app.services.travel import today
from app.schemas import AppSettingsOut, DriverWords, DriverWordsOut, ReplyTemplateIn
from app.services import replies

router = APIRouter(prefix="/app-settings", tags=["settings"], dependencies=[Depends(get_current_user)])

REPLY_TEMPLATE = "customer_reply_template"


async def _out(session: AsyncSession) -> AppSettingsOut:
    template = await session.get(AppSetting, REPLY_TEMPLATE)
    usage = await session.get(RoutesUsage, today())
    return AppSettingsOut(
        route_optimization=settings.route_optimization,
        max_wait_minutes=settings.max_wait_minutes,
        timezone=settings.app_timezone,
        routes_pairs_today=usage.pairs if usage else 0,
        routes_daily_limit=settings.routes_daily_limit,
        maps_browser_key=settings.google_maps_browser_key,
        maps_map_id=settings.google_maps_map_id,
        address_country=settings.address_country,
        address_area=list(delivery_area()) if delivery_area() else None,
        address_towns=[t.strip() for t in settings.address_towns.split(",") if t.strip()],
        reply_template=template.value if template else None,
    )


@router.get("", response_model=AppSettingsOut)
async def get_settings(session: AsyncSession = Depends(get_session)) -> AppSettingsOut:
    """What the web app needs to know: whether route planning is on (ETAs, urgent / deliver by), the waiting
    limit for new orders, and the dispatchers' own wording for replying to customers."""
    return await _out(session)


@router.put("/reply-template", response_model=AppSettingsOut)
async def set_reply_template(body: ReplyTemplateIn, session: AsyncSession = Depends(get_session)) -> AppSettingsOut:
    """Shared by all dispatchers. Empty removes it."""
    text = (body.text or "").strip()
    if text:
        await session.execute(
            insert(AppSetting).values(key=REPLY_TEMPLATE, value=text).on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": text})
        )
    else:
        setting = await session.get(AppSetting, REPLY_TEMPLATE)
        if setting:
            await session.delete(setting)
    await session.commit()
    return await _out(session)


async def _words_out(session: AsyncSession) -> DriverWordsOut:
    return DriverWordsOut(**await replies.load_words(session), defaults=DriverWords(**replies.DEFAULT_WORDS))


@router.get("/driver-words", response_model=DriverWordsOut, dependencies=[Depends(require_admin)])
async def get_driver_words(session: AsyncSession = Depends(get_session)) -> DriverWordsOut:
    """The words drivers reply with: arrived, delivered, on duty, off duty (Plan 5). Administrators only."""
    return await _words_out(session)


@router.put("/driver-words", response_model=DriverWordsOut, dependencies=[Depends(require_admin)])
async def set_driver_words(body: DriverWords, session: AsyncSession = Depends(get_session)) -> DriverWordsOut:
    """Administrators only: one wrong word affects every driver. The bot uses the new words at once. A word can mean
    only one thing, numbers can't be words (they're minutes), and each list needs a word."""
    words = replies.tidy_words(body.model_dump())
    if (problem := replies.words_problem(words)) is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    value = json.dumps(words, ensure_ascii=False)
    await session.execute(
        insert(AppSetting)
        .values(key=replies.WORDS_SETTING, value=value)
        .on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": value})
    )
    await session.commit()
    return await _words_out(session)
