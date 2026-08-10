from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
import uuid


# ── Course schemas ──────────────────────────────────────────
class CourseCreate(BaseModel):
    name: str
    description: Optional[str] = None
    total_lessons: int = 0
    price_per_subscription: Optional[float] = None
    lessons_per_subscription: int = 8


class CourseResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    total_lessons: int
    price_per_subscription: Optional[float]
    lessons_per_subscription: int
    is_active: bool

    class Config:
        from_attributes = True


# ── Parent schemas ──────────────────────────────────────────
class ParentCreate(BaseModel):
    full_name: str
    phone: str
    whatsapp_phone: Optional[str] = None
    email: Optional[str] = None


class ParentUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    email: Optional[str] = None


class ParentResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str
    whatsapp_phone: Optional[str]
    email: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Student schemas ─────────────────────────────────────────
class StudentCreate(BaseModel):
    full_name: str
    birth_date: Optional[date] = None
    current_course_id: Optional[uuid.UUID] = None
    level: str = Field(default="beginner", pattern="^(beginner|intermediate|advanced)$")
    notes: Optional[str] = None
    parent_ids: Optional[List[uuid.UUID]] = []


class StudentUpdate(BaseModel):
    full_name: Optional[str] = None
    birth_date: Optional[date] = None
    current_course_id: Optional[uuid.UUID] = None
    level: Optional[str] = Field(default=None, pattern="^(beginner|intermediate|advanced)$")
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class StudentResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    birth_date: Optional[date]
    current_course_id: Optional[uuid.UUID]
    level: str
    notes: Optional[str]
    is_active: bool
    created_at: datetime
    parents: List[ParentResponse] = []

    class Config:
        from_attributes = True
