from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class RecurringIncome(Base):
    __tablename__ = "recurring_incomes"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_recurring_incomes_amount_positive"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String, nullable=False, default="transfer")
    day_of_month = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RecurringIncomeRecord(Base):
    __tablename__ = "recurring_income_records"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_recurring_income_records_amount_positive"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recurring_income_id = Column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    recurring_period = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String, nullable=False, default="transfer")
    received_date = Column(Date, nullable=False, server_default=func.current_date(), index=True)
    comment = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_cancelled = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(String, nullable=True)
