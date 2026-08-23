from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from core.database import get_db
from core.dependencies import get_current_user
from models.lesson import Lesson
from models.schedule import Event
from models.student import Student
from models.user import User
import uuid

router = APIRouter(tags=["schedule"])


class UpcomingItem(BaseModel):
    item_id: uuid.UUID
    item_type: str
    student_id: Optional[uuid.UUID] = None
    student_name: Optional[str] = None
    title: str
    start_time: datetime
    end_time: datetime
    lesson_kind: Optional[str] = None


@router.get("/schedule/upcoming", response_model=List[UpcomingItem])
async def upcoming_schedule(
    days: int = Query(default=7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    tz = timezone.utc
    now = datetime.now(tz)
    until = now + timedelta(days=days)

    # Занятия
    lessons_q = (
        select(Lesson, Event, Student)
        .join(Event, Event.id == Lesson.event_id)
        .join(Student, Student.id == Lesson.student_id)
        .where(
            Event.start_time >= now,
            Event.start_time < until,
            Event.is_cancelled == False,
            Lesson.status != "cancelled",
        )
        .order_by(Event.start_time)
    )
    lessons_result = await db.execute(lessons_q)

    items = []
    for lesson, event, student in lessons_result.all():
        items.append(UpcomingItem(
            item_id=lesson.id,
            item_type="lesson",
            student_id=student.id,
            student_name=student.full_name,
            title=event.title,
            start_time=event.start_time,
            end_time=event.end_time,
            lesson_kind=lesson.lesson_type,
        ))

    # Личные события
    events_q = (
        select(Event)
        .where(
            Event.event_type != "lesson",
            Event.start_time >= now,
            Event.start_time < until,
            Event.is_cancelled == False,
        )
        .order_by(Event.start_time)
    )
    events_result = await db.execute(events_q)

    for event in events_result.scalars().all():
        items.append(UpcomingItem(
            item_id=event.id,
            item_type="event",
            title=event.title,
            start_time=event.start_time,
            end_time=event.end_time,
        ))

    # Сортируем всё вместе по времени
    items.sort(key=lambda x: x.start_time)
    return items
