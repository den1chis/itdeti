from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import uuid

from core.database import Base


class Parent(Base):
    __tablename__ = "parents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    whatsapp_phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StudentParent(Base):
    __tablename__ = "student_parents"

    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("parents.id", ondelete="CASCADE"), primary_key=True)
