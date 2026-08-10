from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime
import uuid


# ── Subscription schemas ─────────────────────────────────────
class SubscriptionCreate(BaseModel):
    student_id: uuid.UUID
    course_id: uuid.UUID
    total_lessons: int
    price_paid: Optional[float] = None
    payment_status: str = "pending"
    paid_by_parent_id: Optional[uuid.UUID] = None
    kaspi_transaction_id: Optional[str] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    notes: Optional[str] = None


class SubscriptionUpdate(BaseModel):
    total_lessons: Optional[int] = None
    used_lessons: Optional[int] = None
    price_paid: Optional[float] = None
    payment_status: Optional[str] = None
    payment_date: Optional[datetime] = None
    kaspi_transaction_id: Optional[str] = None
    valid_until: Optional[date] = None
    notes: Optional[str] = None


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    course_id: uuid.UUID
    total_lessons: int
    used_lessons: int
    remaining_lessons: int
    price_paid: Optional[float]
    payment_status: str
    payment_date: Optional[datetime]
    kaspi_transaction_id: Optional[str]
    valid_from: Optional[date]
    valid_until: Optional[date]
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Event schemas ────────────────────────────────────────────
class EventCreate(BaseModel):
    title: str
    event_type: str = "lesson"
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    notes: Optional[str] = None


class EventResponse(BaseModel):
    id: uuid.UUID
    title: str
    event_type: str
    start_time: datetime
    end_time: datetime
    location: Optional[str]
    notes: Optional[str]
    is_cancelled: bool

    class Config:
        from_attributes = True


# ── Lesson schemas ───────────────────────────────────────────
class LessonCreate(BaseModel):
    student_id: uuid.UUID
    subscription_id: Optional[uuid.UUID] = None
    course_id: Optional[uuid.UUID] = None
    start_time: datetime
    end_time: datetime
    topic: Optional[str] = None
    notes: Optional[str] = None


class LessonUpdate(BaseModel):
    status: Optional[str] = None
    topic: Optional[str] = None
    is_attended: Optional[bool] = None
    teacher_notes: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class LessonResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    student_id: uuid.UUID
    subscription_id: Optional[uuid.UUID]
    course_id: Optional[uuid.UUID]
    status: str
    topic: Optional[str]
    is_attended: Optional[bool]
    teacher_notes: Optional[str]
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Today schedule item ──────────────────────────────────────
class TodayScheduleItem(BaseModel):
    lesson_id: Optional[uuid.UUID]
    event_id: uuid.UUID
    title: str
    event_type: str
    start_time: datetime
    end_time: datetime
    student_name: Optional[str]
    course_name: Optional[str]
    topic: Optional[str]
    lesson_status: Optional[str]
    is_cancelled: bool