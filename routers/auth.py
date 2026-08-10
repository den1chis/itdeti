from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime, timedelta, timezone
from core.database import get_db
from core.security import verify_password, hash_password, create_access_token, create_refresh_token, decode_token
from core.dependencies import get_current_user
from core.config import settings
from models.user import User
from schemas.auth import LoginRequest, TokenResponse, RefreshRequest, UserResponse
import uuid

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.email == payload.email, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    access_token = create_access_token(str(user.id), user.role)
    refresh_token = create_refresh_token(str(user.id))

    # Сохраняем refresh token в БД
    from models.refresh_token import RefreshToken
    expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db.add(RefreshToken(
        user_id=user.id,
        token=refresh_token,
        expires_at=expires,
    ))
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from models.refresh_token import RefreshToken

    payload_data = decode_token(payload.refresh_token)
    if not payload_data or payload_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token == payload.refresh_token,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    stored = result.scalar_one_or_none()
    if not stored:
        raise HTTPException(status_code=401, detail="Refresh token expired or not found")

    user_result = await db.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Ротация токена
    await db.delete(stored)
    new_access = create_access_token(str(user.id), user.role)
    new_refresh = create_refresh_token(str(user.id))

    expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db.add(RefreshToken(user_id=user.id, token=new_refresh, expires_at=expires))
    await db.commit()

    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout")
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from models.refresh_token import RefreshToken
    await db.execute(
        delete(RefreshToken).where(RefreshToken.token == payload.refresh_token)
    )
    await db.commit()
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user

