from sqlalchemy import CheckConstraint, Column, DateTime, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_payments_amount_positive"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("parents.id", ondelete="SET NULL"), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(SAEnum("kaspi", "cash", "transfer", "other", name="payment_method"), nullable=False, default="cash")
    kaspi_notification_id = Column(UUID(as_uuid=True), ForeignKey("incoming_notifications.id", ondelete="SET NULL"), nullable=True, unique=True)
    request_id = Column(String(64), nullable=True, unique=True, index=True)
    comment = Column(String, nullable=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
