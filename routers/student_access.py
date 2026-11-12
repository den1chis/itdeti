"""Access of a student to the student site.

Night 2 access classes (core.access):
- admin only: create access, PATCH (login, is_active), login suggestion;
- admin + teacher (teacher: only own students, foreign -> 404): status, view password, reset password, unlock.

The password is generated with `secrets` and stored twice: bcrypt hash for login and Fernet-encrypted
(core/student_secrets.py) for viewing. Viewing is logged as a fact only (who, which student), never the value."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.client_ip import client_ip
from core.database import get_db
from core.access import require_admin, require_teacher, student_or_404
from core.security import hash_password
from core.student_secrets import PasswordNotReadable, PasswordViewDisabled, decrypt_password, encrypt_password
from core.student_login import generate_password, is_valid_login, login_candidate, normalize_login
from models.student import Student
from models.student_account import StudentAccount
from models.user import User
from schemas.student_site import AccessCreate, AccessCredentials, AccessPassword, AccessStatus, AccessUpdate, LoginSuggestion

router = APIRouter(tags=["student-access"])
log = logging.getLogger("itdeti.student_access")

LOGIN_RULES = "Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _status(student_id: UUID, account) -> AccessStatus:
    if not account:
        return AccessStatus(student_id=student_id, exists=False)
    return AccessStatus(
        student_id=student_id,
        exists=True,
        login=account.login,
        is_active=account.is_active,
        failed_attempts=account.failed_attempts,
        locked_until=account.locked_until,
        last_login_at=account.last_login_at,
        created_at=account.created_at,
    )


async def _student(db: AsyncSession, student_id: UUID, user: User) -> Student:
    """The student if it is visible to the user (admin: any, teacher: own). Otherwise 404."""
    return await student_or_404(db, user, student_id, "Ученик не найден")


async def _audit(action: str, request: Request, user: User, student: Student, **details) -> None:
    """Audit row for an action on a student's access (never the password itself)."""
    await audit.record(action, actor=user, target_type="student", target_id=student.id, ip=client_ip(request),
                       details={"student": student.full_name, **details})


def _set_password(account: StudentAccount, password: str) -> None:
    account.password_hash = hash_password(password)
    account.password_enc = encrypt_password(password)  # None when STUDENT_PASSWORD_KEY is not set


async def _account(db: AsyncSession, student_id: UUID):
    return await db.scalar(select(StudentAccount).where(StudentAccount.student_id == student_id))


async def _login_taken(db: AsyncSession, login: str, except_student: UUID = None) -> bool:
    query = select(StudentAccount.id).where(StudentAccount.login == login)
    if except_student:
        query = query.where(StudentAccount.student_id != except_student)
    return (await db.scalar(query)) is not None


async def _free_login(db: AsyncSession, full_name: str) -> str:
    for digits, tries in ((2, 20), (3, 60)):
        for _ in range(tries):
            candidate = login_candidate(full_name, digits)
            if not await _login_taken(db, candidate):
                return candidate
    raise HTTPException(409, "Не удалось подобрать свободный логин, введите его вручную")


async def _checked_login(db: AsyncSession, raw: str, student_id: UUID) -> str:
    login = normalize_login(raw)
    if not is_valid_login(login):
        raise HTTPException(400, LOGIN_RULES)
    if await _login_taken(db, login, except_student=student_id):
        raise HTTPException(409, "Такой логин уже занят")
    return login


@router.get("/students/{student_id}/access", response_model=AccessStatus, dependencies=[Depends(require_teacher)])
async def get_access(student_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    await _student(db, student_id, user)
    return _status(student_id, await _account(db, student_id))


@router.get("/students/{student_id}/access/login-suggestion", response_model=LoginSuggestion, dependencies=[Depends(require_admin)])
async def suggest_login(student_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    student = await _student(db, student_id, user)
    return LoginSuggestion(login=await _free_login(db, student.full_name))


@router.post("/students/{student_id}/access", response_model=AccessCredentials, status_code=201, dependencies=[Depends(require_admin)])
async def create_access(student_id: UUID, payload: AccessCreate, request: Request, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    _no_store(response)
    student = await _student(db, student_id, user)
    if await _account(db, student_id):
        raise HTTPException(409, "Доступ уже создан. Можно сбросить пароль.")
    login = (
        await _checked_login(db, payload.login, student_id)
        if payload.login and payload.login.strip()
        else await _free_login(db, student.full_name)
    )
    password = generate_password()
    account = StudentAccount(student_id=student_id, login=login, is_active=True)
    _set_password(account, password)
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Такой логин уже занят или доступ уже создан")
    await db.refresh(account)
    await _audit(audit.STUDENT_ACCESS_CREATED, request, user, student, login=account.login)
    return AccessCredentials(**_status(student_id, account).model_dump(), password=password)


@router.post("/students/{student_id}/access/reset-password", response_model=AccessCredentials, dependencies=[Depends(require_teacher)])
async def reset_password(student_id: UUID, request: Request, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    _no_store(response)
    student = await _student(db, student_id, user)
    account = await _account(db, student_id)
    if not account:
        raise HTTPException(404, "Доступ ещё не создан")
    password = generate_password()
    _set_password(account, password)
    account.failed_attempts = 0
    account.locked_until = None
    await db.commit()
    await db.refresh(account)
    await _audit(audit.STUDENT_PASSWORD_RESET, request, user, student, login=account.login)
    return AccessCredentials(**_status(student_id, account).model_dump(), password=password)


@router.patch("/students/{student_id}/access", response_model=AccessStatus, dependencies=[Depends(require_admin)])
async def update_access(student_id: UUID, payload: AccessUpdate, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    student = await _student(db, student_id, user)
    account = await _account(db, student_id)
    if not account:
        raise HTTPException(404, "Доступ ещё не создан")
    was_active = bool(account.is_active)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("login") is not None:
        account.login = await _checked_login(db, changes["login"], student_id)
    if changes.get("is_active") is not None:
        account.is_active = changes["is_active"]
        if account.is_active:
            account.failed_attempts = 0
            account.locked_until = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Такой логин уже занят")
    await db.refresh(account)
    if bool(account.is_active) != was_active:
        await _audit(audit.STUDENT_ACCESS_ENABLED if account.is_active else audit.STUDENT_ACCESS_DISABLED,
                     request, user, student, login=account.login)
    return _status(student_id, account)


@router.get("/students/{student_id}/access/password", response_model=AccessPassword, dependencies=[Depends(require_teacher)])
async def view_password(student_id: UUID, request: Request, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    """The student's current password in plain text (admin, or the student's own teacher)."""
    _no_store(response)
    student = await _student(db, student_id, user)
    account = await _account(db, student_id)
    if not account:
        raise HTTPException(404, "Доступ ещё не создан")
    try:
        password = decrypt_password(account.password_enc)
    except PasswordViewDisabled:
        raise HTTPException(503, {"code": "password_view_disabled",
                                  "message": "Просмотр паролей выключен на сервере (не задан STUDENT_PASSWORD_KEY). Можно сбросить пароль."})
    except PasswordNotReadable:
        raise HTTPException(409, {"code": "password_not_stored", "message": "Пароль недоступен для просмотра, сбросьте"})
    # Only the fact, never the value.
    log.info("user %s viewed the password of student %s", user.id, student_id)
    await _audit(audit.STUDENT_PASSWORD_VIEWED, request, user, student, login=account.login)
    return AccessPassword(student_id=student_id, login=account.login, password=password)


@router.post("/students/{student_id}/access/unlock", response_model=AccessStatus, dependencies=[Depends(require_teacher)])
async def unlock_access(student_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    """Lift the lockout after wrong passwords (the automatic 15-minute unlock stays as it is)."""
    student = await _student(db, student_id, user)
    account = await _account(db, student_id)
    if not account:
        raise HTTPException(404, "Доступ ещё не создан")
    account.failed_attempts = 0
    account.locked_until = None
    await db.commit()
    await db.refresh(account)
    await _audit(audit.STUDENT_ACCESS_UNLOCKED, request, user, student, login=account.login)
    return _status(student_id, account)
