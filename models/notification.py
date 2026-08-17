from sqlalchemy import Column, String, Boolean, Float, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import uuid
from core.database import Base


class Notification(Base):
    __tablename__ = "incoming_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(
        SAEnum("whatsapp", "kaspi", "sms", "internal", name="notification_source"),
        nullable=False
    )
    raw_text = Column(Text, nullable=False)
    sender_name = Column(String, nullable=True)
    sender_phone = Column(String, nullable=True)
    detected_action = Column(
        SAEnum("payment_received", "reschedule_request", "cancellation", "message", "unknown",
               name="notification_action"),
        default="unknown"
    )
    is_processed = Column(Boolean, default=False)
    related_student_id = Column(UUID(as_uuid=True), nullable=True)
    related_parent_id = Column(UUID(as_uuid=True), nullable=True)
    ai_confidence = Column(Float, nullable=True)
    ai_summary = Column(Text, nullable=True)
    action_taken = Column(String, nullable=True)
    requires_confirmation = Column(Boolean, default=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)