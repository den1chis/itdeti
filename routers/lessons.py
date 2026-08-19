from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import get_current_user, teacher_or_admin
from models.lesson import Lesson
from models.schedule import Event, StudentScheduleSlot
from models.student import Student
from models.user import User
from schemas.schedule import LessonCreate, LessonResponse, LessonUpdate, ScheduleItem

router = APIRouter(tags=["lessons", "schedule"])
LOCAL_TZ = ZoneInfo("Asia/Almaty")


def _occurrence_datetime(day: date, slot: StudentScheduleSlot) -> datetime:
    return datetime.combine(day, slot.start_time).replace(tzinfo=LOCAL_TZ)


async def _ensure_schedule_lessons(db: AsyncSession, start: date, end: date) -> None:
    slots_result = await db.execute(
        select(StudentScheduleSlot, Student)
        .join(Student, Student.id == StudentScheduleSlot.student_id)
        .where(
            Student.is_active.is_(True),
            StudentScheduleSlot.is_active.is_(True),
            StudentScheduleSlot.valid_from <= end,
            or_(StudentScheduleSlot.valid_until.is_(None), StudentScheduleSlot.valid_until >= start),
        )
    )
    for slot, student in slots_result.all():
        first = max(start, slot.valid_from)
        last = min(end, slot.valid_until) if slot.valid_until else end
        current = first + timedelta(days=(slot.day_of_week - first.weekday()) % 7)
        while current <= last:
            start_at = _occurrence_datetime(current, slot)
            existing = await db.scalar(
                select(Lesson).where(
                    Lesson.schedule_slot_id == slot.id,
                    Lesson.original_start_time == start_at,
                )
            )
            if not existing:
                end_at = start_at + timedelta(minutes=slot.duration_minutes)
                event = Event(
                    title=f"Занятие — {student.full_name}",
                    event_type="personal",
                    start_time=start_at,
                    end_time=end_at,
                )
                db.add(event)
                await db.flush()
                db.add(
                    Lesson(
                        student_id=student.id,
                        event_id=event.id,
                        lesson_type="regular",
                        status="scheduled",
                        price=student.lesson_price,
                        schedule_slot_id=slot.id,
                        original_start_time=start_at,
                    )
                )
            current += timedelta(days=7)
    await db.flush()


async def _lesson_response(db: AsyncSession, lesson: Lesson) -> LessonResponse:
    event = await db.scalar(select(Event).where(Event.id == lesson.event_id))
    if not event:
        raise HTTPException(500, "Lesson event is missing")
    return LessonResponse(
        id=lesson.id,
        event_id=lesson.event_id,
        student_id=lesson.student_id,
        lesson_type=lesson.lesson_type,
        status=lesson.status,
        topic=lesson.topic,
        is_attended=lesson.is_attended,
        teacher_notes=lesson.teacher_notes,
        price=lesson.price,
        balance_deducted=lesson.balance_deducted,
        schedule_slot_id=lesson.schedule_slot_id,
        original_start_time=lesson.original_start_time,
        start_time=event.start_time,
        end_time=event.end_time,
        created_at=lesson.created_at,
        updated_at=lesson.updated_at,
    )


@router.post("/lessons", response_model=LessonResponse, status_code=201)
async def create_lesson(payload: LessonCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    student = await db.scalar(select(Student).where(Student.id == payload.student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")
    slot = None
    if payload.schedule_slot_id:
        slot = await db.scalar(
            select(StudentScheduleSlot).where(
                StudentScheduleSlot.id == payload.schedule_slot_id,
                StudentScheduleSlot.student_id == student.id,
            )
        )
        if not slot:
            raise HTTPException(404, "Schedule slot not found")

    price = Decimal("0.00") if payload.lesson_type == "trial" else (
        payload.price if payload.price is not None else Decimal(student.lesson_price)
    )
    end_at = payload.start_time + timedelta(minutes=payload.duration_minutes)
    event = Event(
        title=f"Занятие — {student.full_name}",
        event_type="personal",
        start_time=payload.start_time,
        end_time=end_at,
        notes=payload.teacher_notes,
    )
    db.add(event)
    await db.flush()
    lesson = Lesson(
        student_id=student.id,
        event_id=event.id,
        lesson_type=payload.lesson_type,
        status="scheduled",
        topic=payload.topic,
        teacher_notes=payload.teacher_notes,
        price=price,
        schedule_slot_id=payload.schedule_slot_id,
        original_start_time=(
            _occurrence_datetime(payload.start_time.date(), slot)
            if slot else None
        ),
    )
    db.add(lesson)
    await db.commit()
    await db.refresh(lesson)
    return await _lesson_response(db, lesson)


@router.get("/lessons", response_model=List[LessonResponse])
async def list_lessons(
    student_id: Optional[uuid.UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if from_date and to_date and to_date < from_date:
        raise HTTPException(400, "to_date must be on or after from_date")
    if from_date:
        await _ensure_schedule_lessons(db, from_date, to_date or from_date)
        await db.commit()
    query = select(Lesson).join(Event, Event.id == Lesson.event_id)
    if student_id:
        query = query.where(Lesson.student_id == student_id)
    if from_date:
        query = query.where(Event.start_time >= datetime.combine(from_date, time.min).replace(tzinfo=LOCAL_TZ))
    if to_date:
        query = query.where(Event.start_time < datetime.combine(to_date + timedelta(days=1), time.min).replace(tzinfo=LOCAL_TZ))
    if status:
        query = query.where(Lesson.status == status)
    result = await db.execute(query.order_by(Event.start_time.desc()))
    return [await _lesson_response(db, lesson) for lesson in result.scalars().all()]


@router.patch("/lessons/{lesson_id}", response_model=LessonResponse)
async def update_lesson(
    lesson_id: uuid.UUID,
    payload: LessonUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    lesson = await db.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    event = await db.scalar(select(Event).where(Event.id == lesson.event_id).with_for_update())
    student = await db.scalar(select(Student).where(Student.id == lesson.student_id).with_for_update())
    if not event or not student:
        raise HTTPException(500, "Lesson data is incomplete")

    if payload.start_time is not None:
        duration = payload.duration_minutes or int((event.end_time - event.start_time).total_seconds() // 60)
        event.start_time = payload.start_time
        event.end_time = payload.start_time + timedelta(minutes=duration)
        if lesson.original_start_time is None and lesson.schedule_slot_id:
            lesson.original_start_time = _occurrence_datetime(event.start_time.date(), await db.scalar(
                select(StudentScheduleSlot).where(StudentScheduleSlot.id == lesson.schedule_slot_id)
            ))
        lesson.status = "rescheduled"
    elif payload.duration_minutes is not None:
        event.end_time = event.start_time + timedelta(minutes=payload.duration_minutes)

    for field in ("topic", "is_attended", "teacher_notes", "status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(lesson, field, value)

    should_deduct = lesson.status == "completed" and lesson.lesson_type != "trial"
    if should_deduct and not lesson.balance_deducted:
        student.balance = Decimal(student.balance) - Decimal(lesson.price)
        lesson.balance_deducted = True
    elif not should_deduct and lesson.balance_deducted:
        student.balance = Decimal(student.balance) + Decimal(lesson.price)
        lesson.balance_deducted = False

    await db.commit()
    await db.refresh(lesson)
    return await _lesson_response(db, lesson)


async def _schedule_items(db: AsyncSession, start: date, end: date) -> list[ScheduleItem]:
    await _ensure_schedule_lessons(db, start, end)
    await db.commit()
    lower = datetime.combine(start, time.min).replace(tzinfo=LOCAL_TZ)
    upper = datetime.combine(end + timedelta(days=1), time.min).replace(tzinfo=LOCAL_TZ)

    lesson_rows = await db.execute(
        select(Lesson, Event, Student)
        .join(Event, Event.id == Lesson.event_id)
        .join(Student, Student.id == Lesson.student_id)
        .where(Event.start_time >= lower, Event.start_time < upper)
        .order_by(Event.start_time)
    )
    items = [
        ScheduleItem(
            item_id=lesson.id,
            event_id=event.id,
            item_type="lesson",
            title=event.title,
            start_time=event.start_time,
            end_time=event.end_time,
            student_id=student.id,
            student_name=student.full_name,
            lesson_type=lesson.lesson_type,
            lesson_status=lesson.status,
            price=lesson.price,
            topic=lesson.topic,
            location=None,
            is_cancelled=lesson.status == "cancelled",
        )
        for lesson, event, student in lesson_rows.all()
    ]

    personal_rows = await db.execute(
        select(Event).where(
            Event.is_cancelled.is_(False),
            Event.start_time >= lower,
            Event.start_time < upper,
            ~exists(select(1).where(Lesson.event_id == Event.id)),
        ).order_by(Event.start_time)
    )
    items.extend(
        ScheduleItem(
            item_id=event.id,
            event_id=event.id,
            item_type="event",
            title=event.title,
            start_time=event.start_time,
            end_time=event.end_time,
            location=event.location,
        )
        for event in personal_rows.scalars().all()
    )
    return sorted(items, key=lambda item: item.start_time)


@router.get("/schedule/today", response_model=List[ScheduleItem])
async def today_schedule(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    today = datetime.now(LOCAL_TZ).date()
    return await _schedule_items(db, today, today)


@router.get("/schedule/week", response_model=List[ScheduleItem])
async def week_schedule(
    week_start: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    start = week_start or (datetime.now(LOCAL_TZ).date() - timedelta(days=datetime.now(LOCAL_TZ).weekday()))
    return await _schedule_items(db, start, start + timedelta(days=6))
