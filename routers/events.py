from datetime import datetime
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import get_current_user, teacher_or_admin
from models.lesson import Lesson
from models.schedule import Event
from models.user import User
from schemas.schedule import EventCreate, EventResponse, EventUpdate

router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventResponse, status_code=201)
async def create_event(
    payload: EventCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    event = Event(**payload.model_dump())
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/events", response_model=List[EventResponse])
async def list_events(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    include_cancelled: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Event).where(
        ~Event.id.in_(select(Lesson.event_id).where(Lesson.event_id.is_not(None)))
    )
    if not include_cancelled:
        query = query.where(Event.is_cancelled.is_(False))
    if from_date:
        query = query.where(Event.start_time >= from_date)
    if to_date:
        query = query.where(Event.start_time < to_date)
    result = await db.execute(query.order_by(Event.start_time))
    return result.scalars().all()


@router.patch("/events/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: uuid.UUID,
    payload: EventUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    event = await db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event:
        raise HTTPException(404, "Event not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(event, field, value)
    if event.end_time <= event.start_time:
        raise HTTPException(400, "end_time must be after start_time")
    await db.commit()
    await db.refresh(event)
    return event


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    event = await db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event:
        raise HTTPException(404, "Event not found")
    if await db.scalar(select(Lesson.id).where(Lesson.event_id == event_id)):
        raise HTTPException(400, "Lesson events must be managed through /lessons")
    event.is_cancelled = True
    await db.commit()
