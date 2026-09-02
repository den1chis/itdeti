from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class RecurringExpense(Base):
    __tablename__ = "recurring_expenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    category = Column(
        SAEnum(
            "car", "food", "utilities", "salary", "equipment", "other",
            "rent", "tax", "advertising", "materials", "bank_fee", "transport",
            name="expense_category",
            create_type=False,
        ),
        nullable=False,
    )
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(
        SAEnum("kaspi", "cash", "transfer", "other", name="expense_payment_method", create_type=False),
        nullable=False,
        default="cash",
    )
    day_of_month = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
