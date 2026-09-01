from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, Time, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    event_type = Column(
        SAEnum("personal", "meeting", "reminder", "masterclass", name="event_type"),
        nullable=False,
        default="personal",
    )
    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    end_time = Column(DateTime(timezone=True), nullable=False)
    location = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    color = Column(String(20), nullable=False, default="#4f46e5", server_default="#4f46e5")
    is_cancelled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StudentScheduleSlot(Base):
    __tablename__ = "student_schedule"
    __table_args__ = (
        Index("uq_student_schedule_slot_identity", "student_id", "day_of_week", "start_time", "valid_from", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_kind = Column(SAEnum("lesson", "masterclass", name="schedule_lesson_kind"), nullable=False, default="lesson")
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    color = Column(String(20), nullable=False, default="#4f46e5", server_default="#4f46e5")
    is_active = Column(Boolean, nullable=False, default=True)
    valid_from = Column(Date, nullable=False, server_default=func.current_date())
    valid_until = Column(Date, nullable=True)
