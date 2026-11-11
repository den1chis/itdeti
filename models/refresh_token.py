from sqlalchemy import Column, String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from core.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Legacy column (NOT NULL, unique). Rows written before night 4 hold the token itself here; new rows hold
    # the marker 'sha256:<hash>' and never the token (core/refresh_tokens.py).
    token = Column(String, unique=True, nullable=False)
    # SHA-256 (hex) of the token (migration 014). The server looks tokens up by this column.
    token_hash = Column(String, unique=True, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
