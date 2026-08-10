from sqlalchemy import Column, String, Integer, Numeric, Date, ForeignKey, Enum as SAEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import uuid
from core.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id"), nullable=False)
    total_lessons = Column(Integer, nullable=False)
    used_lessons = Column(Integer, default=0, nullable=False)
    price_paid = Column(Numeric(10, 2), nullable=True)
    payment_status = Column(
        SAEnum("paid", "pending", "overdue", "refunded", name="payment_status"),
        default="pending"
    )
    payment_date = Column(DateTime(timezone=True), nullable=True)
    paid_by_parent_id = Column(UUID(as_uuid=True), ForeignKey("parents.id"), nullable=True)
    kaspi_transaction_id = Column(String, nullable=True)
    valid_from = Column(Date, server_default=func.current_date())
    valid_until = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def remaining_lessons(self):
        return self.total_lessons - self.used_lessons