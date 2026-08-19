from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Numeric, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    lesson_type = Column(
        SAEnum("regular", "trial", "extra", name="lesson_type"),
        nullable=False,
        default="regular",
    )
    status = Column(
        SAEnum(
            "scheduled",
            "completed",
            "cancelled",
            "rescheduled",
            name="lesson_status",
        ),
        nullable=False,
        default="scheduled",
    )
    topic = Column(String, nullable=True)
    is_attended = Column(Boolean, nullable=True)
    teacher_notes = Column(String, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    balance_deducted = Column(Boolean, nullable=False, default=False)
    schedule_slot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_schedule.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_start_time = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
