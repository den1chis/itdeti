from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreate(BaseModel):
    student_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(default="cash", pattern="^(kaspi|cash|transfer|other)$")
    parent_id: Optional[uuid.UUID] = None
    kaspi_notification_id: Optional[uuid.UUID] = None
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    comment: Optional[str] = None
    recorded_at: Optional[datetime] = None


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    parent_id: Optional[uuid.UUID]
    amount: Decimal
    payment_method: str
    kaspi_notification_id: Optional[uuid.UUID]
    request_id: Optional[str]
    comment: Optional[str]
    recorded_at: datetime


class ExpenseCreate(BaseModel):
    category: str = Field(pattern="^(car|food|utilities|salary|equipment|other)$")
    amount: Decimal = Field(gt=0)
    description: Optional[str] = None
    payment_method: str = Field(default="cash", pattern="^(kaspi|cash|transfer|other)$")
    expense_date: date = Field(default_factory=date.today)


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    amount: Decimal
    description: Optional[str]
    payment_method: str
    expense_date: date
    created_at: datetime


class FinanceSummary(BaseModel):
    month_start: date
    month_end: date
    income_from_students: Decimal
    expenses_total: Decimal
    expenses_by_category: dict[str, Decimal]
    net_profit: Decimal
    total_school_balance: Decimal
    scheduled_lessons_this_month: int
    scheduled_income_this_month: Decimal
    forecast_income_remaining: Decimal
    active_students: int
    monthly_forecast_income: Decimal


class TransactionItem(BaseModel):
    id: uuid.UUID
    operation_type: str
    amount: Decimal
    date: datetime
    category: Optional[str] = None
    student_id: Optional[uuid.UUID] = None
    student_name: Optional[str] = None
    payment_method: Optional[str] = None
    description: Optional[str] = None
