from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ParentCreate(BaseModel):
    full_name: str = Field(min_length=1)
    phone: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    email: Optional[str] = None


class ParentUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1)
    phone: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    email: Optional[str] = None


class ParentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    phone: Optional[str]
    whatsapp_phone: Optional[str]
    email: Optional[str]
    created_at: datetime


class StudentCreate(BaseModel):
    full_name: str = Field(min_length=1)
    birth_date: Optional[date] = None
    course: Optional[str] = None
    notes: Optional[str] = None
    balance: Decimal = Field(default=Decimal("0.00"), ge=0)
    lesson_price: Decimal = Field(gt=0)
    parent_ids: List[uuid.UUID] = Field(default_factory=list)


class StudentUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1)
    birth_date: Optional[date] = None
    course: Optional[str] = None
    notes: Optional[str] = None
    balance: Optional[Decimal] = None
    lesson_price: Optional[Decimal] = Field(default=None, gt=0)
    is_active: Optional[bool] = None


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    full_name: str
    birth_date: Optional[date]
    course: Optional[str]
    notes: Optional[str]
    balance: Decimal
    lesson_price: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime
    remaining_lessons: int = 0
    current_month_lessons: int = 0
    current_month_due: Decimal = Decimal("0.00")
    parents: List[ParentResponse] = Field(default_factory=list)


class BalanceResponse(BaseModel):
    student_id: uuid.UUID
    balance: Decimal
    remaining_lessons: int
    current_month_lessons: int
    current_month_due: Decimal
    payments_this_month: Decimal
    monthly_debt: Decimal
