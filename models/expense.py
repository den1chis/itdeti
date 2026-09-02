from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


EXPENSE_CATEGORIES = (
    "car", "food", "utilities", "salary", "equipment", "other",
    "rent", "tax", "advertising", "materials", "bank_fee", "transport",
)


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recurring_expense_id = Column(UUID(as_uuid=True), ForeignKey("recurring_expenses.id", ondelete="SET NULL"), nullable=True, index=True)
    recurring_period = Column(Date, nullable=True, index=True)
    category = Column(SAEnum(*EXPENSE_CATEGORIES, name="expense_category", create_type=False), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    description = Column(String, nullable=True)
    payment_method = Column(
        SAEnum("kaspi", "cash", "transfer", "other", name="expense_payment_method"),
        nullable=False,
        default="cash",
    )
    expense_date = Column(Date, nullable=False, server_default=func.current_date(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_cancelled = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(String, nullable=True)
