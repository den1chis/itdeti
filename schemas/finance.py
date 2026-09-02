from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


PAYMENT_METHOD_PATTERN = "^(kaspi|cash|transfer|other)$"
EXPENSE_CATEGORY_PATTERN = "^(car|food|utilities|salary|equipment|other|rent|tax|advertising|materials|bank_fee|transport)$"


class PaymentCreate(BaseModel):
    student_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(default="cash", pattern=PAYMENT_METHOD_PATTERN)
    parent_id: Optional[uuid.UUID] = None
    kaspi_notification_id: Optional[uuid.UUID] = None
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    comment: Optional[str] = None
    recorded_at: Optional[datetime] = None


class PaymentUpdate(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(pattern=PAYMENT_METHOD_PATTERN)
    comment: Optional[str] = None
    recorded_at: datetime


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
    is_cancelled: bool
    cancelled_at: Optional[datetime]
    cancel_reason: Optional[str]


class ExpenseCreate(BaseModel):
    category: str = Field(pattern=EXPENSE_CATEGORY_PATTERN)
    amount: Decimal = Field(gt=0)
    description: Optional[str] = None
    payment_method: str = Field(default="cash", pattern=PAYMENT_METHOD_PATTERN)
    expense_date: date = Field(default_factory=date.today)


class ExpenseUpdate(BaseModel):
    category: str = Field(pattern=EXPENSE_CATEGORY_PATTERN)
    amount: Decimal = Field(gt=0)
    description: Optional[str] = None
    payment_method: str = Field(pattern=PAYMENT_METHOD_PATTERN)
    expense_date: date


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recurring_expense_id: Optional[uuid.UUID]
    recurring_period: Optional[date]
    category: str
    amount: Decimal
    description: Optional[str]
    payment_method: str
    expense_date: date
    created_at: datetime
    is_cancelled: bool
    cancelled_at: Optional[datetime]
    cancel_reason: Optional[str]


class RecurringExpenseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(pattern=EXPENSE_CATEGORY_PATTERN)
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(default="cash", pattern=PAYMENT_METHOD_PATTERN)
    day_of_month: int = Field(default=1, ge=1, le=31)
    description: Optional[str] = None


class RecurringExpenseUpdate(RecurringExpenseCreate):
    is_active: bool = True


class RecurringExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str
    amount: Decimal
    payment_method: str
    day_of_month: int
    is_active: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class FinanceSummary(BaseModel):
    month_start: date
    month_end: date
    income_from_students: Decimal
    expenses_total: Decimal
    expenses_by_category: dict[str, Decimal]
    net_profit: Decimal
    total_school_balance: Decimal
    student_balances_total: Decimal
    negative_student_balances_total: Decimal
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
    is_cancelled: bool = False
    recurring_expense_id: Optional[uuid.UUID] = None
