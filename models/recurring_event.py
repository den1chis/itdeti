from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class RecurringEvent(Base):
    __tablename__ = "recurring_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    event_type = Column(String, nullable=False, default="personal")
    frequency = Column(String, nullable=False)
    interval = Column(Integer, nullable=False, default=1)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    location = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    color = Column(String(20), nullable=False, default="#64748b", server_default="#64748b")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
