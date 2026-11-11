"""Audit log for the admin (night 4): GET /audit, newest first, 50 rows a page, filter by action and date.

What is written and how: core/audit.py. Rows older than 180 days are deleted here, at most once a day."""
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.access import require_admin
from core.database import get_db
from models.security import AuditLog
from models.user import User

router = APIRouter(tags=["audit"], dependencies=[Depends(require_admin)])
LOCAL_TZ = ZoneInfo("Asia/Almaty")


class AuditItem(BaseModel):
    id: UUID
    created_at: datetime
    actor_user_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    actor_kind: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    ip: Optional[str] = None
    details: Dict[str, Any] = {}


class AuditPage(BaseModel):
    items: List[AuditItem]
    total: int
    limit: int
    offset: int
    actions: List[str]


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    action: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Dates are whole local days (Asia/Almaty), both ends included."""
    await audit.purge_old()
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "date_from must not be later than date_to")
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if date_from:
        conditions.append(AuditLog.created_at >= datetime.combine(date_from, time.min, LOCAL_TZ))
    if date_to:
        conditions.append(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), time.min, LOCAL_TZ))

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    rows = (
        await db.execute(
            select(AuditLog).where(*conditions).order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit).offset(offset)
        )
    ).scalars().all()

    actor_ids = {row.actor_user_id for row in rows if row.actor_user_id}
    names = {}
    if actor_ids:
        for user_id, full_name, email in (
            await db.execute(select(User.id, User.full_name, User.email).where(User.id.in_(actor_ids)))
        ).all():
            names[user_id] = full_name or email
    items = [
        AuditItem(
            id=row.id, created_at=row.created_at, actor_user_id=row.actor_user_id,
            actor_name=names.get(row.actor_user_id), actor_kind=row.actor_kind, action=row.action,
            target_type=row.target_type, target_id=row.target_id, ip=row.ip, details=row.details or {},
        )
        for row in rows
    ]
    return AuditPage(items=items, total=total, limit=limit, offset=offset, actions=list(audit.ACTIONS))
