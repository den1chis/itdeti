"""One-time codes "CRM -> learning platform" (night 5, migration 015).

POST /auth/handoff (a staff member with a live CRM access token) issues a random code (32 bytes, url-safe),
stores ONLY its SHA-256 with the user, the target screen and expires_at = now + 60 s. The CRM opens
https://<platform>/#handoff=<code> (the code is in the fragment: it never reaches a server log or Referer).
POST /auth/handoff/redeem deletes the row atomically (DELETE ... RETURNING): a code works exactly once, even
for two parallel requests. Expired, unknown, reused codes and codes of users that are inactive or no longer
staff are refused with one and the same answer. The platform gets a NEW token pair, never the CRM's tokens.

Expired rows are deleted every time a new code is issued."""
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.rate_limit import SlidingWindowLimiter
from models.security import AuthHandoffCode

CODE_TTL_SECONDS = 60
CODE_BYTES = 32
TARGET_RE = re.compile(r"^(profile|catalog|student:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")
# What a code may look like (secrets.token_urlsafe(32) gives 43 characters); anything else is refused at once.
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")

# Redeem attempts per client IP (in memory, like the other limiters) and codes issued per user.
redeem_limiter = SlidingWindowLimiter(max_calls=10, period_seconds=60)
issue_limiter = SlidingWindowLimiter(max_calls=20, period_seconds=60)


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def valid_target(target: str) -> bool:
    return bool(TARGET_RE.match(target or ""))


async def issue(db: AsyncSession, user_id, target: str, ip: Optional[str]) -> str:
    """Store a new code for the user (the caller commits) and return the code itself (shown once)."""
    now = datetime.now(timezone.utc)
    await db.execute(delete(AuthHandoffCode).where(AuthHandoffCode.expires_at < now))
    code = secrets.token_urlsafe(CODE_BYTES)
    db.add(AuthHandoffCode(
        code_hash=code_hash(code),
        user_id=user_id,
        target=target.lower(),
        expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
        created_ip=ip,
    ))
    return code


@dataclass(frozen=True)
class Redeemed:
    user_id: object
    target: str


async def redeem(db: AsyncSession, code: str) -> Optional[Redeemed]:
    """Consume the code (one use, the caller commits). None if it is malformed, unknown, used or expired."""
    if not CODE_RE.match(code or ""):
        return None
    row = (
        await db.execute(
            delete(AuthHandoffCode)
            .where(AuthHandoffCode.code_hash == code_hash(code))
            .returning(AuthHandoffCode.user_id, AuthHandoffCode.target, AuthHandoffCode.expires_at)
        )
    ).first()
    if not row:
        return None
    user_id, target, expires_at = row
    if expires_at <= datetime.now(timezone.utc):
        return None
    return Redeemed(user_id=user_id, target=target)


async def pending_for(db: AsyncSession, user_id) -> int:
    """Number of stored codes of a user (tests)."""
    from sqlalchemy import func

    return await db.scalar(select(func.count()).select_from(AuthHandoffCode).where(AuthHandoffCode.user_id == user_id)) or 0
