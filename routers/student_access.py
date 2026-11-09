"""Admin: access of a student to the student site (create, reset password, switch on/off, edit login).

The plain password is generated with `secrets`, returned ONCE in the response and never stored."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import admin_only
from core.security import hash_password
from core.student_login import generate_password, is_valid_login, login_candidate, normalize_login
from models.student import Student
from models.student_account import StudentAccount
from schemas.student_site import AccessCreate, AccessCredentials, AccessStatus, AccessUpdate, LoginSuggestion

router = APIRouter(tags=["student-access"], dependencies=[Depends(admin_only)])

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


async def _student(db: AsyncSession, student_id: UUID) -> Student:
    student = await db.scalar(select(Student).where(Student.id == student_id))
    if not student:
        raise HTTPException(404, "Ученик не найден")
    return student


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


@router.get("/students/{student_id}/access", response_model=AccessStatus)
async def get_access(student_id: UUID, db: AsyncSession = Depends(get_db)):
    await _student(db, student_id)
    return _status(student_id, await _account(db, student_id))


@router.get("/students/{student_id}/access/login-suggestion", response_model=LoginSuggestion)
async def suggest_login(student_id: UUID, db: AsyncSession = Depends(get_db)):
    student = await _student(db, student_id)
    return LoginSuggestion(login=await _free_login(db, student.full_name))


@router.post("/students/{student_id}/access", response_model=AccessCredentials, status_code=201)
async def create_access(student_id: UUID, payload: AccessCreate, response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    student = await _student(db, student_id)
    if await _account(db, student_id):
        raise HTTPException(409, "Доступ уже создан. Можно сбросить пароль.")
    login = (
        await _checked_login(db, payload.login, student_id)
        if payload.login and payload.login.strip()
        else await _free_login(db, student.full_name)
    )
    password = generate_password()
    account = StudentAccount(student_id=student_id, login=login, password_hash=hash_password(password), is_active=True)
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Такой логин уже занят или доступ уже создан")
    await db.refresh(account)
    return AccessCredentials(**_status(student_id, account).model_dump(), password=password)


@router.post("/students/{student_id}/access/reset-password", response_model=AccessCredentials)
async def reset_password(student_id: UUID, response: Response, db: AsyncSession = Depends(get_db)):
    _no_store(response)
    await _student(db, student_id)
    account = await _account(db, student_id)
    if not account:
        raise HTTPException(404, "Доступ ещё не создан")
    password = generate_password()
    account.password_hash = hash_password(password)
    account.failed_attempts = 0
    account.locked_until = None
    await db.commit()
    await db.refresh(account)
    return AccessCredentials(**_status(student_id, account).model_dump(), password=password)


@router.patch("/students/{student_id}/access", response_model=AccessStatus)
async def update_access(student_id: UUID, payload: AccessUpdate, db: AsyncSession = Depends(get_db)):
    await _student(db, student_id)
    account = await _account(db, student_id)
    if not account:
        raise HTTPException(404, "Доступ ещё не создан")
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
    return _status(student_id, account)
