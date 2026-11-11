import logging
import time
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit, login_guard, refresh_tokens
from core.access import require_staff
from core.client_ip import client_ip
from core.database import get_db
from core.dependencies import get_current_user
from core.security import create_access_token, create_refresh_token, decode_token, verify_password
from models.user import User
from schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("itdeti.auth")

# Spent when the email does not exist, so both cases cost the same bcrypt time.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()
# Successful logins of the same user from the same IP are written to the audit log at most once per this
# period (the Android app logs in again every time its access token expires; one row a day per device is
# enough to see "who and from where"). Failures and locks are always written.
SUCCESS_AUDIT_INTERVAL_SECONDS = 12 * 60 * 60
_success_logged = {}


def _too_many(seconds: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=login_guard.minutes_text(seconds),
        headers={"Retry-After": str(int(seconds))},
    )


def _success_needs_audit(user_id, ip: str) -> bool:
    now = time.monotonic()
    if len(_success_logged) > 5000:
        _success_logged.clear()
    key = (str(user_id), ip)
    last = _success_logged.get(key)
    if last is not None and now - last < SUCCESS_AUDIT_INTERVAL_SECONDS:
        return False
    _success_logged[key] = now
    return True


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    wait = login_guard.ip_limiter.hit(ip)
    if wait is not None:
        raise _too_many(wait)

    email = login_guard.normalize_email(payload.email)
    wait = await login_guard.blocked_for(db, email, ip)
    if wait is not None:
        raise _too_many(wait)

    # Emails are compared without case and surrounding spaces. Two users that differ only by case must not
    # exist (checked by scripts/prod_ops.py preflight); the oldest one wins if they ever do.
    user = (
        await db.execute(
            select(User)
            .where(func.lower(func.trim(User.email)) == email, User.is_active == True)  # noqa: E712
            .order_by(User.created_at)
            .limit(1)
        )
    ).scalars().first()

    if user:
        password_ok = verify_password(payload.password, user.password_hash)
    else:
        bcrypt.checkpw(payload.password.encode()[:72], _DUMMY_HASH.encode())
        password_ok = False

    if not password_ok:
        user_id = user.id if user else None
        pair_locked, email_locked = await login_guard.register_failure(db, email, ip)  # commits
        await audit.record(audit.LOGIN_FAILED, actor=user_id, actor_kind="user" if user_id else "system",
                           target_type="user", target_id=user_id, ip=ip, details={"email": email})
        if pair_locked or email_locked:
            await audit.record(
                audit.LOGIN_LOCKED, actor=user_id, actor_kind="user" if user_id else "system",
                target_type="user", target_id=user_id, ip=ip,
                details={"email": email, "scope": "email" if email_locked else "email+ip",
                         "minutes": login_guard.LOCK_MINUTES},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    access_token = create_access_token(str(user.id), user.role)
    refresh_token = create_refresh_token(str(user.id))

    # Only the SHA-256 of the refresh token is stored (core/refresh_tokens.py).
    db.add(refresh_tokens.new_row(user.id, refresh_token))
    await login_guard.register_success(db, email, ip)
    await db.commit()

    if _success_needs_audit(user.id, ip):
        await audit.record(audit.LOGIN_SUCCESS, actor=user.id, target_type="user", target_id=user.id, ip=ip)
    await refresh_tokens.purge_expired()
    await login_guard.purge_old()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload_data = decode_token(payload.refresh_token)
    if not payload_data or payload_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # By hash; tokens stored before night 4 are also found by the legacy plain column.
    stored = await refresh_tokens.find_valid(db, payload.refresh_token)
    if not stored:
        raise HTTPException(status_code=401, detail="Refresh token expired or not found")

    user_result = await db.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    # A deactivated user gets no new tokens (is_active may also be NULL in old rows -> treated as inactive,
    # the same as get_current_user does).
    if user.is_active is not True:
        raise HTTPException(status_code=401, detail="User is inactive")

    # Ротация токена: старая запись (в т. ч. в старом открытом формате) удаляется, новая хранится хешем.
    await db.delete(stored)
    new_access = create_access_token(str(user.id), user.role)
    new_refresh = create_refresh_token(str(user.id))
    db.add(refresh_tokens.new_row(user.id, new_refresh))
    await db.commit()

    await refresh_tokens.purge_expired()

    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout")
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    await refresh_tokens.revoke(db, payload.refresh_token)
    await db.commit()
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse, dependencies=[Depends(require_staff)])
async def me(current_user: User = Depends(get_current_user)):
    return current_user
