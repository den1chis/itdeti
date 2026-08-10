from sqlalchemy import Column, String, Boolean, ForeignKey, Enum as SAEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import uuid
from core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    event_type = Column(
        SAEnum("lesson", "personal", "meeting", "reminder", name="event_type"),
        default="lesson"
    )
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    location = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    is_cancelled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id"), nullable=True)
    status = Column(
        SAEnum("scheduled", "completed", "cancelled", "rescheduled", name="lesson_status"),
        default="scheduled"
    )
    topic = Column(String, nullable=True)
    is_attended = Column(Boolean, nullable=True)
    teacher_notes = Column(Text, nullable=True)
    original_time = Column(DateTime(timezone=True), nullable=True)
    rescheduled_from_id = Column(UUID(as_uuid=True), ForeignKey("lessons.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())