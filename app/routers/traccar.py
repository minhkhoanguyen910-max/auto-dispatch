import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Driver
from app.services.roads import road_path

router = APIRouter(prefix="/traccar", tags=["traccar"])

KNOTS_TO_KMH = 1.852


class TraccarPosition(BaseModel):
    latitude: float
    longitude: float
    fixTime: datetime
    # Heading in degrees and speed in knots (Traccar's unit); missing on some devices
    course: float | None = None
    speed: float | None = None


class TraccarDevice(BaseModel):
    uniqueId: str


class TraccarForward(BaseModel):
    position: TraccarPosition
    device: TraccarDevice


@router.post("/positions", status_code=204)
async def receive_position(
    payload: TraccarForward,
    token: str = Query(),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Position forwarding target: Traccar POSTs here for every GPS fix."""
    if not secrets.compare_digest(token, settings.traccar_forward_token):
        raise HTTPException(status_code=403)

    driver = await session.scalar(
        select(Driver).where(Driver.traccar_unique_id == payload.device.uniqueId)
    )
    if driver is None:
        return  # device not linked to a driver yet

    # Traccar can deliver fixes out of order; keep only the newest
    if driver.last_seen_at and payload.position.fixTime <= driver.last_seen_at:
        return

    pos = payload.position
    path = None
    if driver.last_location is not None and driver.last_seen_at is not None:
        loc = func.geometry(Driver.last_location)
        prev = (await session.execute(select(func.ST_Y(loc), func.ST_X(loc)).where(Driver.id == driver.id))).one()
        path = await road_path(tuple(prev), driver.last_seen_at, (pos.latitude, pos.longitude), pos.fixTime)
    driver.last_path = [list(p) for p in path] if path else None
    driver.last_location = f"SRID=4326;POINT({pos.longitude} {pos.latitude})"
    driver.last_seen_at = pos.fixTime
    driver.last_course = pos.course
    driver.last_speed_kmh = round(pos.speed * KNOTS_TO_KMH, 1) if pos.speed is not None else None
    await session.commit()
