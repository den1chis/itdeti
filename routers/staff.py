"""Staff management and teacher assignment (admin only).

- GET/POST /staff, PATCH /staff/{id}, POST /staff/{id}/reset-password
- PUT /students/{id}/teacher   permanent teacher of a student (or null)
- PUT /lessons/{id}/teacher    substitute for ONE future lesson (or null)

Dismissal rule: deactivating a user or taking the teacher role away while students or future substitutions
are still assigned to them -> 409 with the list. On deactivation all refresh tokens of the user are deleted
(access tokens stop working at once anyway: get_current_user checks is_active on every request).
The last active admin can be neither deactivated nor lose the admin role; an admin can do neither to themselves
(409 self_deactivation / self_demotion)."""
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.client_ip import client_ip
from core.access import ADMIN, STAFF_ROLES, TEACHER, active_teacher_or_400, primary_role, require_admin, role_sql, roles_of
from core.database import get_db
from core.dependencies import get_current_user
from core.security import hash_password
from models.lesson import Lesson
from models.refresh_token import RefreshToken
from models.schedule import Event
from models.student import Student
from models.user import User
from schemas.staff import (
    LessonTeacherResponse,
    StaffBlocker,
    StaffCreate,
    StaffCredentials,
    StaffPasswordReset,
    StaffResponse,
    StaffUpdate,
    StudentTeacherResponse,
    TeacherAssign,
)

router = APIRouter(tags=["staff"], dependencies=[Depends(require_admin)])
log = logging.getLogger("itdeti.staff")

PASSWORD_ALPHABET = string.ascii_letters + string.digits
FUTURE_LESSON_STATUSES = ("scheduled", "rescheduled")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(12))


def _is_staff_sql():
    from sqlalchemy import or_

    return or_(*(role_sql(role) for role in STAFF_ROLES))


async def _students_count(db: AsyncSession, user_id: UUID) -> int:
    return await db.scalar(
        select(func.count()).select_from(Student).where(Student.teacher_id == user_id, Student.is_active.is_(True))
    ) or 0


async def _out(db: AsyncSession, user: User) -> StaffResponse:
    order = list(STAFF_ROLES)
    return StaffResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        roles=[r for r in order if r in roles_of(user)],
        is_active=bool(user.is_active),
        students_count=await _students_count(db, user.id),
        created_at=user.created_at,
    )


async def _staff_or_404(db: AsyncSession, user_id: UUID) -> User:
    user = await db.scalar(select(User).where(User.id == user_id, _is_staff_sql()).with_for_update())
    if not user:
        raise HTTPException(404, "Сотрудник не найден")
    return user


async def _blockers(db: AsyncSession, user_id: UUID) -> List[StaffBlocker]:
    """Students (any, also inactive) whose permanent teacher is the user, and future lessons where he substitutes."""
    students = await db.execute(select(Student.id, Student.full_name).where(Student.teacher_id == user_id).order_by(Student.full_name))
    lessons = await db.execute(
        select(Lesson.id, Student.full_name, Event.start_time)
        .join(Event, Event.id == Lesson.event_id)
        .join(Student, Student.id == Lesson.student_id)
        .where(
            Lesson.teacher_id == user_id,
            Lesson.status.in_(FUTURE_LESSON_STATUSES),
            Event.start_time > datetime.now(timezone.utc),
        )
        .order_by(Event.start_time)
    )
    return [StaffBlocker(kind="student", id=sid, name=name) for sid, name in students.all()] + [
        StaffBlocker(kind="lesson", id=lid, name=name, start_time=start) for lid, name, start in lessons.all()
    ]


async def _other_active_admins(db: AsyncSession, user_id: UUID) -> int:
    return await db.scalar(
        select(func.count()).select_from(User).where(User.id != user_id, User.is_active.is_(True), role_sql(ADMIN))
    ) or 0


async def _drop_sessions(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))


# ---------------------------------------------------------------- /staff
@router.get("/staff", response_model=List[StaffResponse])
async def list_staff(include_inactive: bool = True, db: AsyncSession = Depends(get_db)):
    query = select(User).where(_is_staff_sql()).order_by(User.full_name)
    if not include_inactive:
        query = query.where(User.is_active.is_(True))
    return [await _out(db, user) for user in (await db.execute(query)).scalars().all()]


@router.post("/staff", response_model=StaffCredentials, status_code=201)
async def create_staff(
    payload: StaffCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    email = payload.email.strip().lower()
    if await db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Пользователь с таким email уже есть")
    password = payload.password or _generate_password()
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        phone=payload.phone,
        role=primary_role(payload.roles),
        roles=list(payload.roles),
        is_active=True,
        password_hash=hash_password(password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Пользователь с таким email уже есть") from exc
    await db.refresh(user)
    log.info("staff user %s created with roles %s", user.id, ",".join(payload.roles))
    await audit.record(audit.STAFF_CREATED, actor=current_user, target_type="user", target_id=user.id,
                       ip=client_ip(request), details={"name": user.full_name, "email": user.email, "roles": list(user.roles or [])})
    return StaffCredentials(**(await _out(db, user)).model_dump(), password=password)


@router.patch("/staff/{user_id}", response_model=StaffResponse)
async def update_staff(
    user_id: UUID,
    payload: StaffUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = await _staff_or_404(db, user_id)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("full_name", "is_active", "roles"):
        if field in changes and changes[field] is None:
            raise HTTPException(400, f"{field} cannot be null")

    old_roles = roles_of(user)
    was_active = bool(user.is_active)
    new_roles = set(changes["roles"]) if "roles" in changes else old_roles
    deactivating = changes.get("is_active") is False and bool(user.is_active)
    losing_teacher = TEACHER in old_roles and TEACHER not in new_roles
    losing_admin = ADMIN in old_roles and ADMIN not in new_roles

    if user.id == current_user.id:
        # An admin never switches themselves off or takes their own admin role away (another admin can).
        if deactivating:
            raise HTTPException(409, {"code": "self_deactivation", "message": "Нельзя выключить самого себя"})
        if losing_admin:
            raise HTTPException(409, {"code": "self_demotion", "message": "Нельзя снять с себя роль admin"})
    if (deactivating or losing_admin) and ADMIN in old_roles and user.is_active and not await _other_active_admins(db, user.id):
        raise HTTPException(409, {"code": "last_admin", "message": "Это последний активный администратор: его нельзя выключить или лишить роли admin"})
    if deactivating or losing_teacher:
        blockers = await _blockers(db, user.id)
        if blockers:
            raise HTTPException(409, {
                "code": "has_students",
                "message": "Сначала переназначьте учеников и будущие замены этого преподавателя",
                "blockers": [b.model_dump(mode="json") for b in blockers],
            })

    if "full_name" in changes:
        user.full_name = changes["full_name"].strip()
    if "phone" in changes:
        user.phone = changes["phone"]
    if "roles" in changes:
        user.roles = [r for r in STAFF_ROLES if r in new_roles]
        user.role = primary_role(new_roles)
    if "is_active" in changes:
        user.is_active = changes["is_active"]
    if deactivating:
        await _drop_sessions(db, user.id)
    await db.commit()
    await db.refresh(user)
    if deactivating or "roles" in changes:
        log.info("staff user %s updated: active=%s roles=%s", user.id, user.is_active, ",".join(user.roles or []))
    who = {"name": user.full_name, "email": user.email}
    if roles_of(user) != old_roles:
        await audit.record(audit.STAFF_ROLES_CHANGED, actor=current_user, target_type="user", target_id=user.id,
                           ip=client_ip(request),
                           details={**who, "before": [r for r in STAFF_ROLES if r in old_roles], "after": list(user.roles or [])})
    if bool(user.is_active) != was_active:
        await audit.record(audit.STAFF_ENABLED if user.is_active else audit.STAFF_DISABLED, actor=current_user,
                           target_type="user", target_id=user.id, ip=client_ip(request), details=who)
    return await _out(db, user)


@router.post("/staff/{user_id}/reset-password", response_model=StaffCredentials)
async def reset_staff_password(user_id: UUID, response: Response, payload: StaffPasswordReset = None, db: AsyncSession = Depends(get_db)):
    """New password (given by the admin or generated), shown once. All refresh tokens of the user are deleted."""
    _no_store(response)
    user = await _staff_or_404(db, user_id)
    password = (payload.password if payload else None) or _generate_password()
    user.password_hash = hash_password(password)
    await _drop_sessions(db, user.id)
    await db.commit()
    await db.refresh(user)
    log.info("password of staff user %s was reset", user.id)
    return StaffCredentials(**(await _out(db, user)).model_dump(), password=password)


# ---------------------------------------------------------------- teacher assignment
@router.put("/students/{student_id}/teacher", response_model=StudentTeacherResponse)
async def set_student_teacher(student_id: UUID, payload: TeacherAssign, db: AsyncSession = Depends(get_db)):
    student = await db.scalar(select(Student).where(Student.id == student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")
    teacher = await active_teacher_or_400(db, payload.teacher_id)
    student.teacher_id = teacher.id if teacher else None
    await db.commit()
    return StudentTeacherResponse(student_id=student.id, teacher_id=student.teacher_id, teacher_name=teacher.full_name if teacher else None)


@router.put("/lessons/{lesson_id}/teacher", response_model=LessonTeacherResponse)
async def set_lesson_teacher(lesson_id: UUID, payload: TeacherAssign, db: AsyncSession = Depends(get_db)):
    """Substitute teacher for one FUTURE lesson (null removes the substitution). A held lesson keeps the
    teacher recorded when it was held: history is never rewritten here."""
    lesson = await db.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    start = await db.scalar(select(Event.start_time).where(Event.id == lesson.event_id)) if lesson.event_id else None
    if lesson.status not in FUTURE_LESSON_STATUSES or start is None or start <= datetime.now(timezone.utc):
        raise HTTPException(409, "Замену можно назначить только на будущий запланированный урок")
    teacher = await active_teacher_or_400(db, payload.teacher_id)
    lesson.teacher_id = teacher.id if teacher else None
    await db.commit()
    return LessonTeacherResponse(lesson_id=lesson.id, teacher_id=lesson.teacher_id, teacher_name=teacher.full_name if teacher else None)
