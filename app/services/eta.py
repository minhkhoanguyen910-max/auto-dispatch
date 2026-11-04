"""ETA helpers for route planning (Plan 1): local times, the planned arrival range, and the time spent at a stop."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.models import MeetingRequest, MeetingStatus, OrderEvent, OrderStatus

# Learn the time at a stop from this many recent deliveries, else use SERVICE_MINUTES
MIN_DELIVERIES = 20
HISTORY = timedelta(days=30)
# Faster than this from Arrived to Delivered is a mistaken tap or a test, not a real handover
MIN_HANDOVER = timedelta(seconds=30)
# Meeting-point deliveries include waiting for the customer to walk there (Plan 4): not a normal stop
MEETING_POINT = (MeetingStatus.AGREED, MeetingStatus.DELIVERED, MeetingStatus.NOT_THERE)


def zone() -> ZoneInfo:
    return ZoneInfo(settings.app_timezone)


def local_time(value: datetime | None) -> datetime | None:
    """A time without a zone ("14:00" from the order form) is in APP_TIMEZONE; one with a zone is kept."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=zone())


def auto_deliver_by(placed: datetime) -> datetime | None:
    """The automatic "deliver by" for a new order: the longest a customer should wait (MAX_WAIT_MINUTES)."""
    return placed + timedelta(minutes=settings.max_wait_minutes) if settings.max_wait_minutes > 0 else None


def format_local(value: datetime) -> str:
    """"14:00", or "24 Oct 14:00" when it isn't today, in APP_TIMEZONE."""
    local = value.astimezone(zone())
    today = datetime.now(zone()).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%d %b %H:%M").lstrip("0")


def arrival_range(arrival: datetime, now: datetime, estimated: bool) -> datetime:
    """Latest likely arrival: ETA + 2 min + 15% of the time until then, doubled for estimates. Only later,
    never earlier: customers mind late more than early."""
    ahead = max(arrival - now, timedelta(0))
    margin = timedelta(minutes=2) + ahead * 0.15
    return arrival + (margin * 2 if estimated else margin)


async def stop_seconds(session: AsyncSession, now: datetime) -> int:
    """Typical time at a stop: the median Arrived -> Delivered time of the last 30 days' deliveries, once there
    are at least 20; else SERVICE_MINUTES. Deliveries under 30 seconds (mistaken taps, tests) and meeting-point
    deliveries (waiting for the customer isn't stop time) don't count."""
    arrived, delivered = aliased(OrderEvent), aliased(OrderEvent)
    durations = (
        select(func.extract("epoch", delivered.created_at - func.max(arrived.created_at)).label("seconds"))
        .join(arrived, (arrived.order_id == delivered.order_id) & (arrived.to_status == OrderStatus.AT_STOP))
        .where(
            delivered.to_status == OrderStatus.COMPLETED,
            delivered.created_at >= now - HISTORY,
            ~exists().where(MeetingRequest.order_id == delivered.order_id, MeetingRequest.status.in_(MEETING_POINT)),
        )
        .group_by(delivered.id)
        .subquery()
    )
    count, median = (
        await session.execute(
            select(func.count(), func.percentile_cont(0.5).within_group(durations.c.seconds))
            .select_from(durations)
            .where(durations.c.seconds >= MIN_HANDOVER.total_seconds())
        )
    ).one()
    if count < MIN_DELIVERIES or median is None:
        return round(settings.service_minutes * 60)
    return round(median)
