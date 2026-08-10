from sqlalchemy import Column, String, Boolean, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import uuid
from core.database import Base


class Course(Base):
    __tablename__ = "courses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    total_lessons = Column(Integer, default=0)
    price_per_subscription = Column(Numeric(10, 2), nullable=True)
    lessons_per_subscription = Column(Integer, default=8)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
