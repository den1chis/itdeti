from sqlalchemy import Column, String, Boolean, Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import uuid
from core.database import Base


class Student(Base):
    __tablename__ = "students"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    full_name = Column(String, nullable=False)
    birth_date = Column(Date, nullable=True)
    current_course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    level = Column(String, default="beginner")
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
