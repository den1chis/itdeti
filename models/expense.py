from sqlalchemy import CheckConstraint, Column, Date, DateTime, Enum as SAEnum, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category = Column(
        SAEnum("car", "food", "utilities", "salary", "equipment", "other", name="expense_category"),
        nullable=False,
    )
    amount = Column(Numeric(10, 2), nullable=False)
    description = Column(String, nullable=True)
    payment_method = Column(
        SAEnum("kaspi", "cash", "transfer", "other", name="expense_payment_method"),
        nullable=False,
        default="cash",
    )
    expense_date = Column(Date, nullable=False, server_default=func.current_date(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
