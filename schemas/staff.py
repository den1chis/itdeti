"""Schemas of the teacher routes (/teacher/*) and staff management (/staff, teacher assignment).

Teacher schemas are separate on purpose: money and private fields (balance, lesson_price, price,
balance_deducted, payments, student notes, birth date, admin's lesson notes) are NOT part of them at all,
so they cannot leak by accident."""
from datetime import date, datetime, time
from typing import List, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

StaffRole = Literal["admin", "teacher", "methodist"]


# ---------------------------------------------------------------- /teacher/*
class TeacherMe(BaseModel):
    id: uuid.UUID
    full_name: str
    roles: List[str]
    students_count: int


class TeacherParent(BaseModel):
    full_name: str
    phone: Optional[str] = None
    whatsapp_phone: Optional[str] = None


class TeacherStudent(BaseModel):
    id: uuid.UUID
    full_name: str
    level: Optional[str] = None
    course: Optional[str] = None
    is_active: bool
    has_access: bool
    parents: List[TeacherParent] = Field(default_factory=list)


class TeacherScheduleSlot(BaseModel):
    day_of_week: int  # 0 = Monday
    start_time: time
    duration_minutes: int
    valid_from: date
    valid_until: Optional[date] = None


class TeacherStudentDetail(TeacherStudent):
    schedule: List[TeacherScheduleSlot] = Field(default_factory=list)


class TeacherLesson(BaseModel):
    id: uuid.UUID
    # For a substitute lesson only the student's name and course are given: student_id and topic are null.
    is_substitute: bool
    student_id: Optional[uuid.UUID] = None
    student_name: str
    course: Optional[str] = None
    start_time: datetime
    end_time: datetime
    status: str
    topic: Optional[str] = None
    lesson_type: str


# ---------------------------------------------------------------- /staff
def _clean_roles(roles: List[str]) -> List[str]:
    order = ["admin", "teacher", "methodist"]
    unique = set(roles)
    if not unique:
        raise ValueError("at least one role is required")
    return [r for r in order if r in unique]


class StaffCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=50)
    roles: List[StaffRole]
    # Optional: set by the admin. Otherwise the server generates one. Shown once, stored as bcrypt only.
    password: Optional[str] = Field(default=None, min_length=8, max_length=72)

    _roles = field_validator("roles")(_clean_roles)


class StaffUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=50)
    roles: Optional[List[StaffRole]] = None
    is_active: Optional[bool] = None

    @field_validator("roles")
    @classmethod
    def _roles(cls, value):
        return None if value is None else _clean_roles(value)


class StaffPasswordReset(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=72)


class StaffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: Optional[str] = None
    roles: List[str]
    is_active: bool
    students_count: int = 0
    created_at: Optional[datetime] = None


class StaffCredentials(StaffResponse):
    # Shown once (create / reset); never stored in plain text, never viewable later.
    password: str


class StaffBlocker(BaseModel):
    kind: Literal["student", "lesson"]
    id: uuid.UUID
    name: str
    start_time: Optional[datetime] = None


class TeacherAssign(BaseModel):
    teacher_id: Optional[uuid.UUID] = None


class StudentTeacherResponse(BaseModel):
    student_id: uuid.UUID
    teacher_id: Optional[uuid.UUID] = None
    teacher_name: Optional[str] = None


class LessonTeacherResponse(BaseModel):
    lesson_id: uuid.UUID
    teacher_id: Optional[uuid.UUID] = None
    teacher_name: Optional[str] = None
