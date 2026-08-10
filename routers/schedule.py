from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import List, Optional
from datetime import datetime, date, timezone
from core.database import get_db
from core.dependencies import get_current_user
from models.user import User
from models.subscription import Subscription
from models.schedule import Event, Lesson
from models.student import Student
from models.course import Course
from schemas.schedule import (
    SubscriptionCreate, SubscriptionUpdate, SubscriptionResponse,
    LessonCreate, LessonUpdate, LessonResponse,
    EventCreate, EventResponse,
    TodayScheduleItem,
)
import uuid

router = APIRouter(tags=["schedule"])


# ══════════════════════════════════════════
# SUBSCRIPTIONS
# ══════════════════════════════════════════

@router.post("/subscriptions", response_model=SubscriptionResponse, status_code=201)
async def create_subscription(
    payload: SubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    sub = Subscription(**payload.model_dump())
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return _sub_response(sub)


@router.get("/subscriptions", response_model=List[SubscriptionResponse])
async def list_subscriptions(
    student_id: Optional[uuid.UUID] = None,
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Subscription)
    if student_id:
        q = q.where(Subscription.student_id == student_id)
    result = await db.execute(q.order_by(Subscription.created_at.desc()))
    subs = result.scalars().all()
    if active_only:
        subs = [s for s in subs if s.remaining_lessons > 0]
    return [_sub_response(s) for s in subs]


@router.get("/subscriptions/{sub_id}", response_model=SubscriptionResponse)
async def get_subscription(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return _sub_response(sub)


@router.patch("/subscriptions/{sub_id}", response_model=SubscriptionResponse)
async def update_subscription(
    sub_id: uuid.UUID,
    payload: SubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(sub, field, value)
    if payload.payment_status == "paid" and not sub.payment_date:
        sub.payment_date = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return _sub_response(sub)


@router.get("/subscriptions/alerts/low-lessons", response_model=List[SubscriptionResponse])
async def low_lessons_alert(
    threshold: int = 2,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Subscription))
    subs = result.scalars().all()
    return [_sub_response(s) for s in subs if 0 < s.remaining_lessons <= threshold]


def _sub_response(sub: Subscription) -> dict:
    return SubscriptionResponse(
        id=sub.id,
        student_id=sub.student_id,
        course_id=sub.course_id,
        total_lessons=sub.total_lessons,
        used_lessons=sub.used_lessons,
        remaining_lessons=sub.remaining_lessons,
        price_paid=float(sub.price_paid) if sub.price_paid else None,
        payment_status=sub.payment_status,
        payment_date=sub.payment_date,
        kaspi_transaction_id=sub.kaspi_transaction_id,
        valid_from=sub.valid_from,
        valid_until=sub.valid_until,
        notes=sub.notes,
        created_at=sub.created_at,
    )


# ══════════════════════════════════════════
# EVENTS (личные + прочие)
# ══════════════════════════════════════════

@router.post("/events", response_model=EventResponse, status_code=201)
async def create_event(
    payload: EventCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Event).where(Event.is_cancelled == False)
    if from_date:
        q = q.where(Event.start_time >= from_date)
    if to_date:
        q = q.where(Event.start_time <= to_date)
    result = await db.execute(q.order_by(Event.start_time))
    return result.scalars().all()


@router.delete("/events/{event_id}", status_code=204)
async def cancel_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    event.is_cancelled = True
    await db.commit()


# ══════════════════════════════════════════
# LESSONS
# ══════════════════════════════════════════

@router.post("/lessons", response_model=LessonResponse, status_code=201)
async def create_lesson(
    payload: LessonCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    # Создаём Event автоматически
    student_res = await db.execute(select(Student).where(Student.id == payload.student_id))
    student = student_res.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    event = Event(
        title=f"Занятие — {student.full_name}",
        event_type="lesson",
        start_time=payload.start_time,
        end_time=payload.end_time,
        notes=payload.notes,
    )
    db.add(event)
    await db.flush()

    lesson = Lesson(
        event_id=event.id,
        student_id=payload.student_id,
        subscription_id=payload.subscription_id,
        course_id=payload.course_id,
        topic=payload.topic,
    )
    db.add(lesson)
    await db.commit()
    await db.refresh(lesson)

    lesson.start_time = event.start_time
    lesson.end_time = event.end_time
    return lesson


@router.get("/lessons", response_model=List[LessonResponse])
async def list_lessons(
    student_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Lesson, Event).join(Event, Event.id == Lesson.event_id)
    if student_id:
        q = q.where(Lesson.student_id == student_id)
    result = await db.execute(q.order_by(Event.start_time.desc()))
    rows = result.all()
    lessons = []
    for lesson, event in rows:
        lesson.start_time = event.start_time
        lesson.end_time = event.end_time
        lessons.append(lesson)
    return lessons


@router.patch("/lessons/{lesson_id}", response_model=LessonResponse)
async def update_lesson(
    lesson_id: uuid.UUID,
    payload: LessonUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Lesson, Event)
        .join(Event, Event.id == Lesson.event_id)
        .where(Lesson.id == lesson_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson, event = row

    old_status = lesson.status
    for field, value in payload.model_dump(exclude_none=True).items():
        if field in ("start_time", "end_time"):
            setattr(event, field, value)
        else:
            setattr(lesson, field, value)

    # Списываем занятие с абонемента при завершении
    if lesson.status == "completed" and old_status != "completed" and lesson.subscription_id:
        sub_res = await db.execute(select(Subscription).where(Subscription.id == lesson.subscription_id))
        sub = sub_res.scalar_one_or_none()
        if sub:
            sub.used_lessons = min(sub.used_lessons + 1, sub.total_lessons)

    # Возвращаем занятие если отменили завершение
    if old_status == "completed" and lesson.status != "completed" and lesson.subscription_id:
        sub_res = await db.execute(select(Subscription).where(Subscription.id == lesson.subscription_id))
        sub = sub_res.scalar_one_or_none()
        if sub:
            sub.used_lessons = max(sub.used_lessons - 1, 0)

    await db.commit()
    await db.refresh(lesson)
    lesson.start_time = event.start_time
    lesson.end_time = event.end_time
    return lesson


# ══════════════════════════════════════════
# TODAY SCHEDULE
# ══════════════════════════════════════════

@router.get("/schedule/today", response_model=List[TodayScheduleItem])
async def today_schedule(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    today = date.today()
    q = (
        select(Event, Lesson, Student, Course)
        .outerjoin(Lesson, Lesson.event_id == Event.id)
        .outerjoin(Student, Student.id == Lesson.student_id)
        .outerjoin(Course, Course.id == Lesson.course_id)
        .where(
            Event.is_cancelled == False,
            Event.start_time >= datetime.combine(today, datetime.min.time()),
            Event.start_time < datetime.combine(today, datetime.max.time()),
        )
        .order_by(Event.start_time)
    )
    result = await db.execute(q)
    rows = result.all()

    items = []
    for event, lesson, student, course in rows:
        items.append(TodayScheduleItem(
            lesson_id=lesson.id if lesson else None,
            event_id=event.id,
            title=event.title,
            event_type=event.event_type,
            start_time=event.start_time,
            end_time=event.end_time,
            student_name=student.full_name if student else None,
            course_name=course.name if course else None,
            topic=lesson.topic if lesson else None,
            lesson_status=lesson.status if lesson else None,
            is_cancelled=event.is_cancelled,
        ))
    return items