"""Refresh tokens of the CRM are stored as SHA-256 hashes (night 4, migration 014).

- New rows: token_hash = sha256(token); the legacy NOT NULL column "token" gets the marker 'sha256:<hash>'
  (never the token itself).
- Rows written before night 4 hold the token in "token". Migration 014 filled token_hash for them; rows the
  old code wrote after the migration (during the deploy) have token_hash NULL and are found by the legacy
  column. A legacy row is replaced by a hashed one on its first successful use (rotation in /auth/refresh).
- Expired rows are deleted by the server, at most once a day per process (purge_expired); live rows are
  never touched."""
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models.refresh_token import RefreshToken

log = logging.getLogger("itdeti.refresh_tokens")

HASH_PREFIX = "sha256:"
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_last_purge = 0.0


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_row(user_id, token: str) -> RefreshToken:
    digest = token_hash(token)
    return RefreshToken(
        user_id=user_id,
        token=HASH_PREFIX + digest,
        token_hash=digest,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def _matches(token: str):
    # A client never sends the marker itself: a value starting with the prefix is only compared by hash,
    # so the content of the legacy column cannot be replayed as a token.
    by_hash = RefreshToken.token_hash == token_hash(token)
    if token.startswith(HASH_PREFIX):
        return by_hash
    return or_(by_hash, RefreshToken.token == token)


async def find_valid(db: AsyncSession, token: str) -> Optional[RefreshToken]:
    """The stored, not expired row of a token (hash first, then the legacy plain column)."""
    rows = (
        await db.execute(
            select(RefreshToken).where(_matches(token), RefreshToken.expires_at > datetime.now(timezone.utc))
        )
    ).scalars().all()
    for row in rows:  # prefer the hashed row if both exist
        if row.token_hash:
            return row
    return rows[0] if rows else None


async def revoke(db: AsyncSession, token: str) -> None:
    await db.execute(delete(RefreshToken).where(_matches(token)))


async def purge_expired(force: bool = False) -> Optional[int]:
    """Delete EXPIRED rows only; at most once a day per process, in its own session. Never raises.
    Returns the number of deleted rows, or None when it was not this call's turn (or on error)."""
    global _last_purge
    now = time.monotonic()
    if not force and _last_purge and now - _last_purge < PURGE_INTERVAL_SECONDS:
        return None
    _last_purge = now
    try:
        from core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(RefreshToken).where(RefreshToken.expires_at < datetime.now(timezone.utc))
            )
            await db.commit()
        log.info("expired refresh tokens deleted: %s", result.rowcount)
        return result.rowcount
    except Exception:  # cleanup must never break login/refresh
        log.exception("refresh token cleanup failed")
        return None
