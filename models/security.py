"""Night 4 (migration 014): CRM login throttling state and the audit log."""
import uuid

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from core.database import Base


class AuthLoginState(Base):
    """One row per (email, client IP), see core/login_guard.py."""
    __tablename__ = "auth_login_state"

    email = Column(Text, primary_key=True)
    ip = Column(Text, primary_key=True)
    failed_count = Column(Integer, nullable=False, default=0, server_default="0")
    locked_until = Column(DateTime(timezone=True))
    last_failed_at = Column(DateTime(timezone=True))
    window_started_at = Column(DateTime(timezone=True))
    window_failed = Column(Integer, nullable=False, default=0, server_default="0")
    last_success_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuthEmailLock(Base):
    __tablename__ = "auth_email_locks"

    email = Column(Text, primary_key=True)
    locked_until = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_user_id = Column(UUID(as_uuid=True))
    actor_kind = Column(String, nullable=False, default="user", server_default="user")
    action = Column(String, nullable=False)
    target_type = Column(String)
    target_id = Column(String)
    ip = Column(String)
    details = Column(JSONB, nullable=False, default=dict, server_default="{}")
