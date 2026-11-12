"""Throttling of the CRM login POST /auth/login (night 4, migration 014). State lives in the database, so it
survives restarts and Render's sleep.

Three levels:
1. per IP (in memory): at most 10 login attempts a minute from one client IP (ip_limiter);
2. per (email, IP) pair: 5 wrong passwords in a row -> the PAIR is refused for 15 minutes. A stranger's IP
   never locks the owner out on the owner's own devices. A successful login resets the pair;
3. per email: 30 wrong passwords within 15 minutes from any addresses -> the email is refused for 15 minutes
   for every IP except "trusted" ones (a successful login from that IP within the last 30 days).

Refused attempts (429) are not counted and do not prolong a lock. Unknown emails go through exactly the same
steps as existing ones (same answers, a bcrypt check of the same cost), so the answer does not reveal whether
an email exists.

Emergency unlock (SQL): docs/RUNBOOK_EMERGENCY.md."""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.rate_limit import SlidingWindowLimiter
from models.security import AuthEmailLock, AuthLoginState

log = logging.getLogger("itdeti.login_guard")

PAIR_MAX_FAILURES = 5
LOCK_MINUTES = 15
EMAIL_MAX_FAILURES = 30
EMAIL_WINDOW_MINUTES = 15
TRUSTED_IP_DAYS = 30
IP_MAX_ATTEMPTS_PER_MINUTE = 10
STATE_RETENTION_DAYS = 31  # > TRUSTED_IP_DAYS: a row is useless after that
PURGE_INTERVAL_SECONDS = 24 * 60 * 60

ip_limiter = SlidingWindowLimiter(max_calls=IP_MAX_ATTEMPTS_PER_MINUTE, period_seconds=60)
_last_purge = 0.0


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _seconds_left(until: datetime, now: datetime) -> int:
    return max(1, int((until - now).total_seconds()) + 1)


def minutes_text(seconds: int) -> str:
    """429 message; N is rounded up to whole minutes."""
    minutes = max(1, -(-int(seconds) // 60))
    if minutes % 10 == 1 and minutes % 100 != 11:
        word = "минуту"
    elif minutes % 10 in (2, 3, 4) and minutes % 100 not in (12, 13, 14):
        word = "минуты"
    else:
        word = "минут"
    return f"Слишком много попыток входа. Попробуйте через {minutes} {word}."


async def blocked_for(db: AsyncSession, email: str, ip: str, now: Optional[datetime] = None) -> Optional[int]:
    """Seconds the (email, ip) must still wait, or None when a login attempt may be checked."""
    now = now or datetime.now(timezone.utc)
    state = await db.scalar(select(AuthLoginState).where(AuthLoginState.email == email, AuthLoginState.ip == ip))
    if state and state.locked_until and state.locked_until > now:
        return _seconds_left(state.locked_until, now)
    email_lock = await db.scalar(select(AuthEmailLock).where(AuthEmailLock.email == email))
    if email_lock and email_lock.locked_until > now:
        trusted = bool(
            state and state.last_success_at and state.last_success_at > now - timedelta(days=TRUSTED_IP_DAYS)
        )
        if not trusted:
            return _seconds_left(email_lock.locked_until, now)
    return None


_FAILURE_UPSERT = text(
    """
    INSERT INTO auth_login_state AS s
        (email, ip, failed_count, last_failed_at, window_started_at, window_failed, updated_at)
    VALUES (:email, :ip, 1, :now, :now, 1, :now)
    ON CONFLICT (email, ip) DO UPDATE SET
        failed_count = s.failed_count + 1,
        last_failed_at = :now,
        window_started_at = CASE WHEN s.window_started_at IS NULL OR s.window_started_at < :window_start
                                 THEN :now ELSE s.window_started_at END,
        window_failed = CASE WHEN s.window_started_at IS NULL OR s.window_started_at < :window_start
                             THEN 1 ELSE s.window_failed + 1 END,
        updated_at = :now
    RETURNING failed_count
    """
)
_PAIR_LOCK = text(
    "UPDATE auth_login_state SET locked_until = :until, failed_count = 0, updated_at = :now "
    "WHERE email = :email AND ip = :ip"
)
_EMAIL_LOCK_UPSERT = text(
    """
    INSERT INTO auth_email_locks AS l (email, locked_until, updated_at) VALUES (:email, :until, :now)
    ON CONFLICT (email) DO UPDATE SET locked_until = :until, updated_at = :now
    """
)
_SUCCESS_UPSERT = text(
    """
    INSERT INTO auth_login_state AS s (email, ip, failed_count, last_success_at, updated_at)
    VALUES (:email, :ip, 0, :now, :now)
    ON CONFLICT (email, ip) DO UPDATE SET
        failed_count = 0, locked_until = NULL, window_failed = 0, window_started_at = NULL,
        last_success_at = :now, updated_at = :now
    """
)


async def register_failure(db: AsyncSession, email: str, ip: str, now: Optional[datetime] = None) -> Tuple[bool, bool]:
    """Count a wrong password and COMMIT (the caller raises 401 afterwards, and get_db rolls back on raise).
    Returns (pair_locked_now, email_locked_now)."""
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=EMAIL_WINDOW_MINUTES)
    until = now + timedelta(minutes=LOCK_MINUTES)
    failed = (
        await db.execute(_FAILURE_UPSERT, {"email": email, "ip": ip, "now": now, "window_start": window_start})
    ).scalar_one()
    pair_locked = failed >= PAIR_MAX_FAILURES
    if pair_locked:
        await db.execute(_PAIR_LOCK, {"email": email, "ip": ip, "until": until, "now": now})
    recent = await db.scalar(
        select(func.coalesce(func.sum(AuthLoginState.window_failed), 0)).where(
            AuthLoginState.email == email, AuthLoginState.window_started_at >= window_start
        )
    )
    email_locked = False
    if recent >= EMAIL_MAX_FAILURES:
        existing = await db.scalar(select(AuthEmailLock).where(AuthEmailLock.email == email))
        if not existing or existing.locked_until <= now:
            await db.execute(_EMAIL_LOCK_UPSERT, {"email": email, "until": until, "now": now})
            email_locked = True
    await db.commit()
    return pair_locked, email_locked


async def register_success(db: AsyncSession, email: str, ip: str, now: Optional[datetime] = None) -> None:
    """Reset the pair and remember the IP as trusted for this email. No commit: the caller commits together
    with the new refresh token."""
    now = now or datetime.now(timezone.utc)
    await db.execute(_SUCCESS_UPSERT, {"email": email, "ip": ip, "now": now})


async def purge_old(force: bool = False) -> Optional[int]:
    """Drop state rows nobody needs any more (no activity for 31 days) and expired email locks; at most once
    a day per process, in its own session. Never raises."""
    global _last_purge
    mono = time.monotonic()
    if not force and _last_purge and mono - _last_purge < PURGE_INTERVAL_SECONDS:
        return None
    _last_purge = mono
    try:
        from core.database import AsyncSessionLocal

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(AuthLoginState).where(AuthLoginState.updated_at < now - timedelta(days=STATE_RETENTION_DAYS))
            )
            await db.execute(delete(AuthEmailLock).where(AuthEmailLock.locked_until < now - timedelta(days=1)))
            await db.commit()
        return result.rowcount
    except Exception:
        log.exception("login state cleanup failed")
        return None
