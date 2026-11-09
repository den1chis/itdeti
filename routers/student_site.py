"""Student site API (/student/...): login and the student's own cabinet.

Every data route takes the student id ONLY from the token (get_current_student), never from the
request, and checks the enrollment before returning a course or a lesson."""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.lms_utils import get_embed_url
from core.rate_limit import SlidingWindowLimiter
from core.security import verify_password
from core.student_auth import CurrentStudent, create_student_token, get_current_student
from core.student_login import normalize_login
from models.lms import LmsCourse, LmsEnrollment, LmsLesson, LmsLessonProgress, LmsMaterial, LmsModule, LmsTask
from models.student import Student
from models.student_account import StudentAccount
from schemas.student_site import (
    StudentCourseDetail,
    StudentCourseItem,
    StudentLessonBrief,
    StudentLessonDetail,
    StudentLoginRequest,
    StudentLoginResponse,
    StudentMaterial,
    StudentMe,
    StudentModule,
    StudentProgressResponse,
    StudentProgressUpdate,
    StudentTask,
)

router = APIRouter(prefix="/student", tags=["student-site"])

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
# Per-IP limit on the login route (in memory, see core/rate_limit.py).
login_limiter = SlidingWindowLimiter(max_calls=10, period_seconds=60)
# Used to spend the same bcrypt time when the login does not exist.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def _error(code: int, kind: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": kind, "message": message, **extra})


def _minutes_left(until: datetime, now: datetime) -> int:
    return max(1, -(-int((until - now).total_seconds()) // 60))


# ---------------------------------------------------------------- login
@router.post("/auth/login", response_model=StudentLoginResponse)
async def student_login(payload: StudentLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    wait = login_limiter.hit(ip)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Слишком много попыток входа. Подождите минуту и попробуйте снова.",
                "retry_after_seconds": wait,
            },
            headers={"Retry-After": str(wait)},
        )

    login = normalize_login(payload.login)
    account = await db.scalar(select(StudentAccount).where(StudentAccount.login == login).with_for_update())
    bad_credentials = _error(
        status.HTTP_401_UNAUTHORIZED,
        "invalid_credentials",
        "Неверный логин или пароль",
    )
    if not account:
        bcrypt.checkpw(payload.password.encode()[:72], _DUMMY_HASH.encode())
        raise bad_credentials

    now = datetime.now(timezone.utc)
    if account.locked_until and account.locked_until > now:
        minutes = _minutes_left(account.locked_until, now)
        raise _error(
            status.HTTP_423_LOCKED,
            "locked",
            f"Вход заблокирован на {minutes} мин. из-за неверных паролей. Попробуйте позже.",
            retry_after_minutes=minutes,
        )

    if not verify_password(payload.password, account.password_hash):
        account.failed_attempts = (account.failed_attempts or 0) + 1
        locked = account.failed_attempts >= MAX_FAILED_ATTEMPTS
        if locked:
            account.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            account.failed_attempts = 0
        await db.commit()  # keep the counter: get_db rolls back when the route raises
        if locked:
            raise _error(
                status.HTTP_423_LOCKED,
                "locked",
                f"Вход заблокирован на {LOCK_MINUTES} мин. из-за неверных паролей. Попробуйте позже.",
                retry_after_minutes=LOCK_MINUTES,
            )
        raise bad_credentials

    student = await db.scalar(select(Student).where(Student.id == account.student_id))
    if not account.is_active or not student or not student.is_active:
        await db.rollback()
        raise _error(status.HTTP_403_FORBIDDEN, "disabled", "Доступ выключен. Обратитесь к преподавателю.")

    account.failed_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    await db.commit()
    return StudentLoginResponse(
        access_token=create_student_token(account.student_id),
        expires_in=settings.STUDENT_TOKEN_EXPIRE_MINUTES * 60,
        full_name=student.full_name,
    )


# ---------------------------------------------------------------- helpers (always scoped by student_id)
async def _enrollment(db: AsyncSession, student_id: UUID, course_id: UUID) -> Optional[LmsEnrollment]:
    return await db.scalar(
        select(LmsEnrollment).where(LmsEnrollment.student_id == student_id, LmsEnrollment.course_id == course_id)
    )


async def _ordered_course(db: AsyncSession, course_id: UUID) -> List[Tuple[LmsModule, List[LmsLesson]]]:
    modules = (
        await db.execute(
            select(LmsModule).where(LmsModule.course_id == course_id).order_by(LmsModule.sort_order, LmsModule.created_at)
        )
    ).scalars().all()
    if not modules:
        return []
    lessons = (
        await db.execute(
            select(LmsLesson)
            .where(LmsLesson.module_id.in_([m.id for m in modules]))
            .order_by(LmsLesson.sort_order, LmsLesson.created_at)
        )
    ).scalars().all()
    by_module: Dict[UUID, List[LmsLesson]] = {m.id: [] for m in modules}
    for lesson in lessons:
        by_module[lesson.module_id].append(lesson)
    return [(m, by_module[m.id]) for m in modules]


async def _statuses(db: AsyncSession, student_id: UUID, lesson_ids: List[UUID]) -> Dict[UUID, str]:
    if not lesson_ids:
        return {}
    rows = await db.execute(
        select(LmsLessonProgress.lesson_id, LmsLessonProgress.status).where(
            LmsLessonProgress.student_id == student_id, LmsLessonProgress.lesson_id.in_(lesson_ids)
        )
    )
    return {lesson_id: st for lesson_id, st in rows}


def _course_item(course: LmsCourse, enrollment: LmsEnrollment, total: int, done: int) -> dict:
    return dict(
        id=course.id,
        title=course.title,
        description=course.description,
        course_type=course.course_type,
        enrollment_status=enrollment.status,
        lessons_total=total,
        lessons_done=done,
    )


async def _lesson_for_student(db: AsyncSession, student_id: UUID, lesson_id: UUID):
    """Lesson + module + course, only if the student is enrolled in that course; else 404 (no hint that it exists)."""
    row = (
        await db.execute(
            select(LmsLesson, LmsModule, LmsCourse)
            .join(LmsModule, LmsModule.id == LmsLesson.module_id)
            .join(LmsCourse, LmsCourse.id == LmsModule.course_id)
            .join(
                LmsEnrollment,
                (LmsEnrollment.course_id == LmsCourse.id) & (LmsEnrollment.student_id == student_id),
            )
            .where(LmsLesson.id == lesson_id)
        )
    ).first()
    if not row:
        raise _error(status.HTTP_404_NOT_FOUND, "not_found", "Урок не найден")
    return row


# ---------------------------------------------------------------- cabinet
@router.get("/me", response_model=StudentMe)
async def student_me(current: CurrentStudent = Depends(get_current_student)):
    return StudentMe(full_name=current.full_name)


@router.get("/courses", response_model=List[StudentCourseItem])
async def student_courses(
    current: CurrentStudent = Depends(get_current_student), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(LmsCourse, LmsEnrollment)
            .join(LmsEnrollment, LmsEnrollment.course_id == LmsCourse.id)
            .where(LmsEnrollment.student_id == current.student_id)
            .order_by(LmsEnrollment.started_at.desc())
        )
    ).all()
    result = []
    for course, enrollment in rows:
        tree = await _ordered_course(db, course.id)
        lesson_ids = [lesson.id for _, lessons in tree for lesson in lessons]
        statuses = await _statuses(db, current.student_id, lesson_ids)
        done = sum(1 for lid in lesson_ids if statuses.get(lid) == "done")
        result.append(StudentCourseItem(**_course_item(course, enrollment, len(lesson_ids), done)))
    return result


@router.get("/courses/{course_id}", response_model=StudentCourseDetail)
async def student_course(
    course_id: UUID, current: CurrentStudent = Depends(get_current_student), db: AsyncSession = Depends(get_db)
):
    enrollment = await _enrollment(db, current.student_id, course_id)
    course = await db.scalar(select(LmsCourse).where(LmsCourse.id == course_id)) if enrollment else None
    if not enrollment or not course:
        raise _error(status.HTTP_404_NOT_FOUND, "not_found", "Курс не найден")
    tree = await _ordered_course(db, course_id)
    lesson_ids = [lesson.id for _, lessons in tree for lesson in lessons]
    statuses = await _statuses(db, current.student_id, lesson_ids)
    modules = [
        StudentModule(
            id=module.id,
            title=module.title,
            description=module.description,
            lessons=[
                StudentLessonBrief(
                    id=lesson.id,
                    title=lesson.title,
                    description=lesson.description,
                    duration_min=lesson.duration_min,
                    status=statuses.get(lesson.id, "not_started"),
                )
                for lesson in lessons
            ],
        )
        for module, lessons in tree
    ]
    done = sum(1 for lid in lesson_ids if statuses.get(lid) == "done")
    return StudentCourseDetail(**_course_item(course, enrollment, len(lesson_ids), done), modules=modules)


@router.get("/lessons/{lesson_id}", response_model=StudentLessonDetail)
async def student_lesson(
    lesson_id: UUID, current: CurrentStudent = Depends(get_current_student), db: AsyncSession = Depends(get_db)
):
    lesson, module, course = await _lesson_for_student(db, current.student_id, lesson_id)
    ordered = [l.id for _, lessons in await _ordered_course(db, course.id) for l in lessons]
    index = ordered.index(lesson.id)
    materials = (
        await db.execute(
            select(LmsMaterial).where(LmsMaterial.lesson_id == lesson.id).order_by(LmsMaterial.sort_order, LmsMaterial.created_at)
        )
    ).scalars().all()
    tasks = (
        await db.execute(
            select(LmsTask).where(LmsTask.lesson_id == lesson.id).order_by(LmsTask.sort_order, LmsTask.created_at)
        )
    ).scalars().all()
    statuses = await _statuses(db, current.student_id, [lesson.id])
    return StudentLessonDetail(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        duration_min=lesson.duration_min,
        status=statuses.get(lesson.id, "not_started"),
        course_id=course.id,
        course_title=course.title,
        module_id=module.id,
        module_title=module.title,
        prev_lesson_id=ordered[index - 1] if index > 0 else None,
        next_lesson_id=ordered[index + 1] if index + 1 < len(ordered) else None,
        materials=[
            StudentMaterial(
                id=m.id,
                kind=m.kind,
                title=m.title,
                url=m.url,
                embed_url=get_embed_url(m.url) if m.kind == "link" else None,
                body=m.body,
            )
            for m in materials
        ],
        tasks=[
            StudentTask(
                id=t.id,
                kind=t.kind,
                title=t.title,
                statement=t.statement,
                starter_code=t.starter_code,
                hint=t.hint,
            )
            for t in tasks
        ],
    )


@router.post("/lessons/{lesson_id}/progress", response_model=StudentProgressResponse)
async def student_lesson_progress(
    lesson_id: UUID,
    payload: StudentProgressUpdate,
    current: CurrentStudent = Depends(get_current_student),
    db: AsyncSession = Depends(get_db),
):
    await _lesson_for_student(db, current.student_id, lesson_id)
    progress = await db.scalar(
        select(LmsLessonProgress).where(
            LmsLessonProgress.student_id == current.student_id, LmsLessonProgress.lesson_id == lesson_id
        )
    )
    if progress is None:
        progress = LmsLessonProgress(student_id=current.student_id, lesson_id=lesson_id, status=payload.status)
        db.add(progress)
    elif not (progress.status == "done" and payload.status == "in_progress"):
        # Opening a finished lesson again must not undo "done"; a teacher can still reset it in the CRM.
        progress.status = payload.status
    await db.commit()
    return StudentProgressResponse(lesson_id=lesson_id, status=progress.status)
