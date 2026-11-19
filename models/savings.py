"""Savings (migration 016, night 6): deposits and cash in whole tenge. New tables only, no relationship()
on purpose, so nothing here can affect mapper configuration of the existing models."""
import uuid

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from core.database import Base

ACCOUNT_KINDS = ("deposit", "cash")
MOVEMENT_KINDS = ("deposit", "withdrawal")


class SavingsAccount(Base):
    __tablename__ = "savings_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    is_archived = Column(Boolean, nullable=False, default=False, server_default="false")
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SavingsMovement(Base):
    __tablename__ = "savings_movements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("savings_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    kind = Column(String, nullable=False)
    amount = Column(BigInteger, nullable=False)
    movement_date = Column(Date, nullable=False, index=True)
    comment = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_cancelled = Column(Boolean, nullable=False, default=False, server_default="false")
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
