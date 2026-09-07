from datetime import date, datetime, time, timedelta
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import get_current_user, teacher_or_admin
from models.schedule import Event
from models.recurring_event import RecurringEvent
from models.user import User

router = APIRouter(prefix="/recurring-events", tags=["recurring-events"])


class RecurringEventCreate(BaseModel):
    title: str = Field(min_length=1)
    event_type: str = Field(default="personal", pattern="^(personal|meeting|reminder|masterclass)$")
    frequency: str = Field(pattern="^(daily|weekly)$")
    interval: int = Field(default=1, ge=1, le=52)
    start_date: date
    end_date: date
    start_time: time
    duration_minutes: int = Field(default=60, ge=15, le=480)
    location: Optional[str] = None
    notes: Optional[str] = None
    color: str = Field(default="#64748b", pattern=r"^#[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class RecurringEventResponse(BaseModel):
    id: uuid.UUID
    title: str
    event_type: str
    frequency: str
    interval: int
    start_date: date
    end_date: date
    start_time: time
    duration_minutes: int
    location: Optional[str]
    notes: Optional[str]
    color: str
    is_active: bool


@router.post("", response_model=RecurringEventResponse, status_code=201)
async def create_recurring_event(
    payload: RecurringEventCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    series = RecurringEvent(**payload.model_dump())
    db.add(series)
    await db.flush()

    current = payload.start_date
    while current <= payload.end_date:
        start = datetime.combine(current, payload.start_time).astimezone()
        end = start + timedelta(minutes=payload.duration_minutes)
        db.add(Event(
            title=payload.title,
            event_type=payload.event_type,
            start_time=start,
            end_time=end,
            location=payload.location,
            notes=payload.notes,
            color=payload.color,
        ))
        if payload.frequency == "daily":
            current += timedelta(days=payload.interval)
        else:
            current += timedelta(weeks=payload.interval)

    await db.commit()
    await db.refresh(series)
    return series


@router.get("", response_model=list[RecurringEventResponse])
async def list_recurring_events(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(RecurringEvent).order_by(RecurringEvent.start_date.desc()))
    return result.scalars().all()


@router.delete("/{recurring_id}", response_model=RecurringEventResponse)
async def deactivate_recurring_event(
    recurring_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    series = await db.scalar(select(RecurringEvent).where(RecurringEvent.id == recurring_id).with_for_update())
    if not series:
        raise HTTPException(404, "Recurring event not found")
    series.is_active = False
    await db.commit()
    await db.refresh(series)
    return series
