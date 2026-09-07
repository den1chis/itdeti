from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.dependencies import teacher_or_admin
from models.lesson import Lesson
from models.schedule import Event
from models.student import Student
from models.user import User
from schemas.schedule import LessonResponse

router = APIRouter(prefix="/manual-lessons", tags=["manual-lessons"])
LOCAL_TZ = ZoneInfo("Asia/Almaty")


class ManualLessonCreate(BaseModel):
    student_id: uuid.UUID
    start_time: datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)
    lesson_kind: str = Field(default="lesson", pattern="^(lesson|masterclass)$")
    lesson_type: str = Field(default="regular", pattern="^(regular|trial|extra)$")
    topic: Optional[str] = None
    teacher_notes: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, ge=0)
    color: str = Field(default="#4f46e5", pattern=r"^#[0-9A-Fa-f]{6}$")


def _response(lesson: Lesson, event: Event) -> LessonResponse:
    return LessonResponse(
        id=lesson.id,
        event_id=lesson.event_id,
        student_id=lesson.student_id,
        lesson_kind=lesson.lesson_kind,
        lesson_type=lesson.lesson_type,
        status=lesson.status,
        topic=lesson.topic,
        is_attended=lesson.is_attended,
        teacher_notes=lesson.teacher_notes,
        price=lesson.price,
        color=lesson.color or event.color,
        balance_deducted=lesson.balance_deducted,
        schedule_slot_id=None,
        original_start_time=lesson.original_start_time,
        start_time=event.start_time,
        end_time=event.end_time,
        created_at=lesson.created_at,
        updated_at=lesson.updated_at,
    )


@router.post("", response_model=LessonResponse, status_code=201)
async def create_manual_lesson(
    payload: ManualLessonCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    student = await db.scalar(
        select(Student).where(Student.id == payload.student_id, Student.is_active.is_(True)).with_for_update()
    )
    if not student:
        raise HTTPException(404, "Active student not found")

    start = payload.start_time if payload.start_time.tzinfo else payload.start_time.replace(tzinfo=LOCAL_TZ)
    existing = await db.scalar(
        select(Lesson).where(
            Lesson.student_id == student.id,
            Lesson.original_start_time == start,
            Lesson.schedule_slot_id.is_(None),
        )
    )
    if existing:
        event = await db.scalar(select(Event).where(Event.id == existing.event_id))
        return _response(existing, event)

    price = Decimal("0.00") if payload.lesson_type == "trial" else (
        payload.price if payload.price is not None else Decimal(student.lesson_price)
    )
    end = start + timedelta(minutes=payload.duration_minutes)
    title = f"{student.full_name} — {'Мастер-класс' if payload.lesson_kind == 'masterclass' else 'Урок'}"
    event = Event(
        title=title,
        event_type="personal",
        start_time=start,
        end_time=end,
        notes=payload.teacher_notes,
        color=payload.color,
    )
    db.add(event)
    await db.flush()

    lesson = Lesson(
        student_id=student.id,
        event_id=event.id,
        lesson_kind=payload.lesson_kind,
        lesson_type=payload.lesson_type,
        status="completed",
        topic=payload.topic,
        is_attended=True,
        teacher_notes=payload.teacher_notes,
        price=price,
        color=payload.color,
        balance_deducted=payload.lesson_type != "trial",
        schedule_slot_id=None,
        original_start_time=start,
    )
    db.add(lesson)
    if payload.lesson_type != "trial":
        student.balance = Decimal(student.balance) - price

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.scalar(
            select(Lesson).where(
                Lesson.student_id == student.id,
                Lesson.original_start_time == start,
                Lesson.schedule_slot_id.is_(None),
            )
        )
        if existing:
            event = await db.scalar(select(Event).where(Event.id == existing.event_id))
            return _response(existing, event)
        raise HTTPException(409, "A lesson already exists at this date and time") from exc

    await db.refresh(lesson)
    await db.refresh(event)
    return _response(lesson, event)
