"""Schemas of the night 6 finance routes (period summary, forecast, savings). Savings amounts are whole tenge."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SAVINGS_AMOUNT = 1_000_000_000_000  # same limit as the CHECK in 016_savings.sql
MIN_DATE, MAX_DATE = date(2000, 1, 1), date(2100, 12, 31)


def _clean_text(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"не длиннее {limit} символов")
    return value or None


def _check_date(value: date) -> date:
    if not MIN_DATE <= value <= MAX_DATE:
        raise ValueError("дата вне допустимого диапазона")
    return value


# ---------------------------------------------------------------- period summary
class PeriodSummary(BaseModel):
    date_from: date
    date_to: date
    total_income: Decimal
    income_from_students: Decimal
    recurring_income_total: Decimal
    expenses_total: Decimal
    net: Decimal
    expenses_by_category: dict[str, Decimal]


# ---------------------------------------------------------------- forecast
class ForecastItem(BaseModel):
    name: str
    amount: Decimal


class ScheduleForecast(BaseModel):
    total: Decimal
    students_income: Decimal
    lessons_count: int
    recurring_income: Decimal
    recurring_items: List[ForecastItem]


class AverageMonth(BaseModel):
    month: str
    students_income: Decimal
    recurring_income: Decimal
    total: Decimal


class AverageForecast(BaseModel):
    total: Decimal
    students_income: Decimal
    recurring_income: Decimal
    months: List[AverageMonth]


class Forecast(BaseModel):
    month_start: date
    month_end: date
    default_mode: str = "schedule"
    schedule: ScheduleForecast
    average: AverageForecast


# ---------------------------------------------------------------- savings
class SavingsAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(pattern="^(deposit|cash)$")
    opening_balance: int = Field(default=0, ge=0, le=MAX_SAVINGS_AMOUNT)
    opening_date: Optional[date] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = _clean_text(value, 100)
        if not value:
            raise ValueError("укажите название")
        return value

    @field_validator("opening_date")
    @classmethod
    def _date(cls, value):
        return _check_date(value) if value else value


class SavingsAccountUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_archived: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        if value is None:
            return value
        value = _clean_text(value, 100)
        if not value:
            raise ValueError("укажите название")
        return value


class SavingsAccountResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    is_archived: bool
    balance: int
    movements_count: int
    created_at: datetime


class SavingsOverview(BaseModel):
    total: int
    accounts: List[SavingsAccountResponse]


class SavingsMovementCreate(BaseModel):
    account_id: uuid.UUID
    kind: str = Field(pattern="^(deposit|withdrawal)$")
    amount: int = Field(gt=0, le=MAX_SAVINGS_AMOUNT)
    movement_date: date
    comment: Optional[str] = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def _comment(cls, value):
        return _clean_text(value, 500)

    @field_validator("movement_date")
    @classmethod
    def _date(cls, value):
        return _check_date(value)


class SavingsMovementUpdate(BaseModel):
    kind: str = Field(pattern="^(deposit|withdrawal)$")
    amount: int = Field(gt=0, le=MAX_SAVINGS_AMOUNT)
    movement_date: date
    comment: Optional[str] = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def _comment(cls, value):
        return _clean_text(value, 500)

    @field_validator("movement_date")
    @classmethod
    def _date(cls, value):
        return _check_date(value)


class SavingsMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str = ""
    kind: str
    amount: int
    movement_date: date
    comment: Optional[str]
    is_cancelled: bool
    created_at: datetime


class SavingsMonth(BaseModel):
    month: str
    deposited: int
    withdrawn: int
    end_balance: int


class SavingsMonthly(BaseModel):
    date_from: date
    date_to: date
    months: List[SavingsMonth]
