from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScheduleSlotCreate(BaseModel):
    lesson_kind: str = Field(default="lesson", pattern="^(lesson|masterclass)$")
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    duration_minutes: int = Field(default=60, ge=15, le=480)
    valid_from: date = Field(default_factory=date.today)
    valid_until: Optional[date] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on or after valid_from")
        return self


class ScheduleSlotUpdate(BaseModel):
    lesson_kind: Optional[str] = Field(default=None, pattern="^(lesson|masterclass)$")
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    is_active: Optional[bool] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None


class ScheduleSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    lesson_kind: str
    day_of_week: int
    start_time: time
    duration_minutes: int
    is_active: bool
    valid_from: date
    valid_until: Optional[date]


class LessonCreate(BaseModel):
    student_id: uuid.UUID
    lesson_kind: str = Field(default="lesson", pattern="^(lesson|masterclass)$")
    lesson_type: str = Field(default="regular", pattern="^(regular|trial|extra)$")
    start_time: datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)
    topic: Optional[str] = None
    teacher_notes: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, ge=0)
    schedule_slot_id: Optional[uuid.UUID] = None


class LessonUpdate(BaseModel):
    lesson_kind: Optional[str] = Field(default=None, pattern="^(lesson|masterclass)$")
    lesson_type: Optional[str] = Field(default=None, pattern="^(regular|trial|extra)$")
    status: Optional[str] = Field(default=None, pattern="^(scheduled|completed|cancelled|rescheduled)$")
    topic: Optional[str] = None
    is_attended: Optional[bool] = None
    teacher_notes: Optional[str] = None
    start_time: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    price: Optional[Decimal] = Field(default=None, ge=0)


class LessonResponse(BaseModel):
    id: uuid.UUID
    event_id: Optional[uuid.UUID]
    student_id: uuid.UUID
    lesson_kind: str
    lesson_type: str
    status: str
    topic: Optional[str]
    is_attended: Optional[bool]
    teacher_notes: Optional[str]
    price: Decimal
    balance_deducted: bool
    schedule_slot_id: Optional[uuid.UUID]
    original_start_time: Optional[datetime]
    start_time: datetime
    end_time: datetime
    created_at: datetime
    updated_at: datetime


class ScheduleItem(BaseModel):
    item_id: Optional[uuid.UUID]
    event_id: Optional[uuid.UUID]
    item_type: str
    title: str
    start_time: datetime
    end_time: datetime
    student_id: Optional[uuid.UUID] = None
    student_name: Optional[str] = None
    lesson_kind: Optional[str] = None
    lesson_type: Optional[str] = None
    lesson_status: Optional[str] = None
    lesson_price: Optional[Decimal] = None
    topic: Optional[str] = None
    location: Optional[str] = None
    is_cancelled: bool = False


class EventCreate(BaseModel):
    title: str = Field(min_length=1)
    event_type: str = Field(default="personal", pattern="^(personal|meeting|reminder)$")
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_time(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class EventUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1)
    event_type: Optional[str] = Field(default=None, pattern="^(personal|meeting|reminder)$")
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    is_cancelled: Optional[bool] = None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    event_type: str
    start_time: datetime
    end_time: datetime
    location: Optional[str]
    notes: Optional[str]
    is_cancelled: bool
    created_at: datetime
    updated_at: datetime
