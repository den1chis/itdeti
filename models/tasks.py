"""Tasks / to-do models (migration 011). New tables only, no relationship() on purpose,
so nothing here can affect mapper configuration of the existing models."""
import uuid

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.sql import func

from core.database import Base


class TaskProject(Base):
    __tablename__ = "task_projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    color = Column(String(20), nullable=False, default="#4f46e5")
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("task_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    due_date = Column(Date, nullable=True, index=True)
    is_done = Column(Boolean, nullable=False, default=False)
    done_at = Column(DateTime(timezone=True), nullable=True)
    repeat = Column(String, nullable=False, default="none")
    repeat_interval = Column(Integer, nullable=False, default=1)
    repeat_weekdays = Column(ARRAY(SmallInteger), nullable=False, default=list)
    repeat_until = Column(Date, nullable=True)
    source = Column(String, nullable=False, default="manual")
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TaskOccurrence(Base):
    """Per-occurrence override of a recurring task, keyed by the ORIGINAL occurrence date."""
    __tablename__ = "task_occurrences"

    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    occurrence_date = Column(Date, primary_key=True)
    done_at = Column(DateTime(timezone=True), nullable=True)
    moved_to = Column(Date, nullable=True, index=True)
    is_skipped = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
