"""Audit log (night 4, migration 014): only the most sensitive actions.

record(...) writes one row in its OWN session after the main action has been committed. It never raises:
a failed write is logged on the server and the action stays done. details must not hold passwords, tokens
or keys (keys with such names are dropped as a safety net).

Rows older than 180 days are deleted by the server when GET /audit is called, at most once a day."""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete

from models.security import AuditLog

log = logging.getLogger("itdeti.audit")

RETENTION_DAYS = 180
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_last_purge = 0.0

# Action codes (the CRM shows a Russian label for each, audit.js).
LOGIN_SUCCESS = "login_success"
LOGIN_FAILED = "login_failed"
LOGIN_LOCKED = "login_locked"
STUDENT_PASSWORD_VIEWED = "student_password_viewed"
STUDENT_PASSWORD_RESET = "student_password_reset"
STUDENT_ACCESS_CREATED = "student_access_created"
STUDENT_ACCESS_ENABLED = "student_access_enabled"
STUDENT_ACCESS_DISABLED = "student_access_disabled"
STUDENT_ACCESS_UNLOCKED = "student_access_unlocked"
STAFF_CREATED = "staff_created"
STAFF_ROLES_CHANGED = "staff_roles_changed"
STAFF_DISABLED = "staff_disabled"
STAFF_ENABLED = "staff_enabled"
STUDENT_DELETED = "student_deleted"
PAYMENT_DELETED = "payment_deleted"
EXPENSE_DELETED = "expense_deleted"

ACTIONS = (
    LOGIN_SUCCESS, LOGIN_FAILED, LOGIN_LOCKED,
    STUDENT_PASSWORD_VIEWED, STUDENT_PASSWORD_RESET, STUDENT_ACCESS_CREATED, STUDENT_ACCESS_ENABLED,
    STUDENT_ACCESS_DISABLED, STUDENT_ACCESS_UNLOCKED,
    STAFF_CREATED, STAFF_ROLES_CHANGED, STAFF_DISABLED, STAFF_ENABLED,
    STUDENT_DELETED, PAYMENT_DELETED, EXPENSE_DELETED,
)

_SECRET_WORDS = ("password", "token", "secret", "key", "hash")


def _clean(details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    clean = {}
    for key, value in (details or {}).items():
        if any(word in str(key).lower() for word in _SECRET_WORDS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            clean[str(key)] = value
        elif isinstance(value, (list, tuple)):
            clean[str(key)] = [str(v)[:200] for v in value][:50]
        else:
            clean[str(key)] = str(value)[:500]
    return clean


async def record(
    action: str,
    *,
    actor=None,
    actor_kind: str = "user",
    target_type: Optional[str] = None,
    target_id=None,
    ip: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Write one audit row. `actor` is a User, a user id or None. Returns False (and logs) on any error."""
    try:
        from core.database import AsyncSessionLocal

        actor_id = getattr(actor, "id", actor)
        async with AsyncSessionLocal() as db:
            db.add(AuditLog(
                actor_user_id=actor_id,
                actor_kind=actor_kind,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                ip=ip,
                details=_clean(details),
            ))
            await db.commit()
        return True
    except Exception:
        log.exception("audit write failed (action=%s); the action itself is not affected", action)
        return False


async def purge_old(force: bool = False) -> Optional[int]:
    """Delete rows older than RETENTION_DAYS; at most once a day per process. Never raises."""
    global _last_purge
    now = time.monotonic()
    if not force and _last_purge and now - _last_purge < PURGE_INTERVAL_SECONDS:
        return None
    _last_purge = now
    try:
        from core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(AuditLog).where(AuditLog.created_at < datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS))
            )
            await db.commit()
        log.info("audit rows older than %s days deleted: %s", RETENTION_DAYS, result.rowcount)
        return result.rowcount
    except Exception:
        log.exception("audit cleanup failed")
        return None
