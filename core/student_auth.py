"""Student tokens and the dependency for student-site routes.

A student token carries ONLY role=student, student_id and exp (short lifetime, no refresh token).
CRM dependencies (core.dependencies.get_current_user) reject such tokens; student routes accept nothing else."""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db

STUDENT_ROLE = "student"

student_bearer = HTTPBearer(auto_error=False)


def create_student_token(student_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.STUDENT_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"role": STUDENT_ROLE, "student_id": str(student_id), "exp": expire},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_student_token(token: str) -> Optional[uuid.UUID]:
    """Return the student UUID from a valid student token, else None (CRM tokens are rejected)."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
    if payload.get("role") != STUDENT_ROLE or "sub" in payload or payload.get("type"):
        return None
    try:
        return uuid.UUID(str(payload.get("student_id")))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CurrentStudent:
    student_id: uuid.UUID
    full_name: str


def _unauthorized(message: str = "Войдите заново") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_student(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(student_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentStudent:
    if not credentials:
        raise _unauthorized()
    student_id = decode_student_token(credentials.credentials)
    if not student_id:
        raise _unauthorized()

    from models.student import Student
    from models.student_account import StudentAccount

    row = (
        await db.execute(
            select(Student.full_name, Student.is_active, StudentAccount.is_active)
            .join(StudentAccount, StudentAccount.student_id == Student.id)
            .where(Student.id == student_id)
        )
    ).first()
    # Access switched off (or student deactivated) -> the token stops working immediately.
    if not row or not row[1] or not row[2]:
        raise _unauthorized("Доступ выключен. Обратитесь к преподавателю.")
    return CurrentStudent(student_id=student_id, full_name=row[0])
