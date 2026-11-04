from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import DriverMessage
from app.schemas import DriverMessageOut

router = APIRouter(prefix="/driver-messages", tags=["driver messages"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[DriverMessageOut])
async def list_driver_messages(
    after: int | None = Query(None, description="Only messages newer than this id (the web's alerts poll with it)"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[DriverMessageOut]:
    """Drivers' messages to the bot, newest first (Plan 5): what they wrote, the order, what the bot understood and
    what it did."""
    query = select(DriverMessage).order_by(DriverMessage.id.desc()).limit(limit)
    if after is not None:
        query = query.where(DriverMessage.id > after)
    return [
        DriverMessageOut(
            id=m.id,
            driver=m.driver.name,
            order_id=m.order_id,
            order_number=m.order.shown_number if m.order else None,
            text=m.text,
            meaning=m.meaning,
            result=m.result,
            created_at=m.created_at,
        )
        for m in await session.scalars(query)
    ]
