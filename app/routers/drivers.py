from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.db import get_session
from app.models import Driver
from app.schemas import DriverCreate, DriverOut, DriverSetupInfo, DriverUpdate
from app.services import telegram, traccar
from app.services.drivers import new_link_code, new_traccar_id

router = APIRouter(prefix="/drivers", tags=["drivers"], dependencies=[Depends(get_current_user)])


async def _call_traccar(call: Awaitable[None]) -> None:
    try:
        await call
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't update the GPS server (Traccar). Try again.")


# Faster than walking pace counts as driving (GPS shows a few km/h even when parked)
MOVING_KMH = 5


async def _to_out(session: AsyncSession, driver: Driver) -> DriverOut:
    lat = lng = None
    if driver.last_location is not None:
        lat, lng = (
            await session.execute(
                select(
                    func.ST_Y(func.geometry(Driver.last_location)), func.ST_X(func.geometry(Driver.last_location))
                ).where(Driver.id == driver.id)
            )
        ).one()
    bot = await telegram.bot_username() if driver.link_code else None
    online_since = datetime.now(UTC) - timedelta(minutes=settings.driver_online_minutes)
    return DriverOut(
        id=driver.id,
        name=driver.name,
        phone=driver.phone,
        traccar_unique_id=driver.traccar_unique_id,
        is_active=driver.is_active,
        recorded_by_dispatcher=driver.recorded_by_dispatcher,
        on_duty=driver.on_duty,
        telegram_connected=driver.telegram_user_id is not None,
        telegram_link=f"https://t.me/{bot}?start={driver.link_code}" if bot else None,
        online=driver.last_seen_at is not None and driver.last_seen_at >= online_since,
        last_seen_at=driver.last_seen_at,
        latitude=lat,
        longitude=lng,
        course=driver.last_course,
        speed_kmh=driver.last_speed_kmh,
        moving=(driver.last_speed_kmh or 0) >= MOVING_KMH,
        path=driver.last_path,
        created_at=driver.created_at,
    )


async def _get(session: AsyncSession, driver_id: int) -> Driver:
    driver = await session.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found")
    return driver


async def _ensure_id_free(session: AsyncSession, traccar_id: str, exclude_id: int | None = None) -> None:
    query = select(Driver.id).where(Driver.traccar_unique_id == traccar_id)
    if exclude_id is not None:
        query = query.where(Driver.id != exclude_id)
    if await session.scalar(query):
        raise HTTPException(status.HTTP_409_CONFLICT, "Another driver already uses this identifier")


@router.get("/setup-info", response_model=DriverSetupInfo)
async def setup_info() -> DriverSetupInfo:
    """What drivers need to set up their phone: Traccar server URL and the bot's username."""
    try:
        bot = await telegram.bot_username()
    except httpx.HTTPError:
        bot = None
    return DriverSetupInfo(
        traccar_client_url=settings.traccar_client_url,
        traccar_interval_seconds=settings.traccar_interval_seconds,
        traccar_distance_meters=settings.traccar_distance_meters,
        traccar_accuracy=settings.traccar_accuracy,
        bot_username=bot,
    )


@router.get("", response_model=list[DriverOut])
async def list_drivers(session: AsyncSession = Depends(get_session)) -> list[DriverOut]:
    drivers = await session.scalars(select(Driver).order_by(Driver.is_active.desc(), Driver.name))
    return [await _to_out(session, d) for d in drivers]


@router.post("", response_model=DriverOut, status_code=201)
async def create_driver(body: DriverCreate, session: AsyncSession = Depends(get_session)) -> DriverOut:
    traccar_id = body.traccar_unique_id
    if traccar_id:
        await _ensure_id_free(session, traccar_id)
    else:
        traccar_id = new_traccar_id()
        while await session.scalar(select(Driver.id).where(Driver.traccar_unique_id == traccar_id)):
            traccar_id = new_traccar_id()

    name = body.name.strip()
    await _call_traccar(traccar.ensure_device(traccar_id, name))
    driver = Driver(
        name=name,
        phone=(body.phone or "").strip() or None,
        traccar_unique_id=traccar_id,
        recorded_by_dispatcher=body.recorded_by_dispatcher,
        # Recorded drivers don't connect Telegram
        link_code=None if body.recorded_by_dispatcher else new_link_code(),
    )
    session.add(driver)
    await session.commit()
    await session.refresh(driver)
    return await _to_out(session, driver)


@router.patch("/{driver_id}", response_model=DriverOut)
async def update_driver(driver_id: int, body: DriverUpdate, session: AsyncSession = Depends(get_session)) -> DriverOut:
    driver = await _get(session, driver_id)
    new_name = body.name.strip() if body.name is not None else driver.name
    new_id = body.traccar_unique_id or driver.traccar_unique_id

    if new_id != driver.traccar_unique_id:
        await _ensure_id_free(session, new_id, exclude_id=driver.id)
        await _call_traccar(traccar.change_device_id(driver.traccar_unique_id, new_id, new_name))
        driver.traccar_unique_id = new_id
        # Old position came from the old phone/identifier
        driver.last_location = None
        driver.last_seen_at = None
        driver.last_path = None
    elif new_name != driver.name:
        await _call_traccar(traccar.ensure_device(new_id, new_name))

    driver.name = new_name
    if "phone" in body.model_fields_set:
        driver.phone = (body.phone or "").strip() or None
    if body.is_active is not None:
        driver.is_active = body.is_active
    if body.recorded_by_dispatcher is not None:
        driver.recorded_by_dispatcher = body.recorded_by_dispatcher
    if body.on_duty is not None:
        # Coming on duty: Automatic orders waiting for a driver go to them on the bot's next round
        driver.on_duty = body.on_duty
    await session.commit()
    await session.refresh(driver)
    return await _to_out(session, driver)


@router.post("/{driver_id}/telegram-link", response_model=DriverOut)
async def new_telegram_link(driver_id: int, session: AsyncSession = Depends(get_session)) -> DriverOut:
    """Issue a fresh one-time link, e.g. when the driver gets a new phone or Telegram account.

    The current connection keeps working until the new link is used.
    """
    driver = await _get(session, driver_id)
    driver.link_code = new_link_code()
    await session.commit()
    await session.refresh(driver)
    return await _to_out(session, driver)


@router.delete("/{driver_id}/telegram", response_model=DriverOut)
async def disconnect_telegram(driver_id: int, session: AsyncSession = Depends(get_session)) -> DriverOut:
    """Stop sending orders to this driver's Telegram account (e.g. lost phone)."""
    driver = await _get(session, driver_id)
    driver.telegram_user_id = None
    await session.commit()
    await session.refresh(driver)
    return await _to_out(session, driver)
