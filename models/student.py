from sqlalchemy import Column, String, Boolean, Date, DateTime, ForeignKey, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from core.database import Base


class Student(Base):
    __tablename__ = "students"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    full_name = Column(String, nullable=False)
    birth_date = Column(Date, nullable=True)
    level = Column(String, default="beginner", nullable=True)
    notes = Column(Text, nullable=True)
    balance = Column(Numeric(10, 2), nullable=False, default=0)
    lesson_price = Column(Numeric(10, 2), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
