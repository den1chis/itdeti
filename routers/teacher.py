"""Teacher routes (/teacher/*): read only, own students and own lessons, separate response schemas.

Access class: admin + teacher (core.access.require_teacher). For a teacher "own" means students.teacher_id = me;
admin sees everything. Money and private fields are not in the schemas at all (schemas/staff.py).
Personal events, tasks and notifications never appear here."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.access import effective_teacher_sql, is_admin, owned_student_ids, require_teacher, roles_of, student_or_404
from core.database import get_db
from models.lesson import Lesson
from models.parent import Parent, StudentParent
from models.schedule import Event, StudentScheduleSlot
from models.student import Student
from models.student_account import StudentAccount
from models.user import User
from schemas.staff import TeacherLesson, TeacherMe, TeacherParent, TeacherScheduleSlot, TeacherStudent, TeacherStudentDetail

router = APIRouter(prefix="/teacher", tags=["teacher"], dependencies=[Depends(require_teacher)])

MAX_RANGE_DAYS = 62


async def _students_out(db: AsyncSession, students: List[Student]) -> List[TeacherStudent]:
    ids = [s.id for s in students]
    parents = defaultdict(list)
    accounts = set()
    if ids:
        rows = await db.execute(
            select(StudentParent.student_id, Parent.full_name, Parent.phone, Parent.whatsapp_phone)
            .join(Parent, Parent.id == StudentParent.parent_id)
            .where(StudentParent.student_id.in_(ids))
            .order_by(Parent.full_name)
        )
        for student_id, full_name, phone, whatsapp in rows.all():
            parents[student_id].append(TeacherParent(full_name=full_name, phone=phone, whatsapp_phone=whatsapp))
        accounts = set((await db.execute(select(StudentAccount.student_id).where(StudentAccount.student_id.in_(ids)))).scalars().all())
    return [
        TeacherStudent(
            id=s.id,
            full_name=s.full_name,
            level=s.level,
            course=s.course,
            is_active=s.is_active,
            has_access=s.id in accounts,
            parents=parents[s.id],
        )
        for s in students
    ]


@router.get("/me", response_model=TeacherMe)
async def teacher_me(db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    count = await db.scalar(
        select(func.count()).select_from(Student).where(Student.id.in_(owned_student_ids(user)), Student.is_active.is_(True))
    )
    order = ["admin", "teacher", "methodist"]
    return TeacherMe(id=user.id, full_name=user.full_name, roles=[r for r in order if r in roles_of(user)], students_count=count or 0)


@router.get("/students", response_model=List[TeacherStudent])
async def teacher_students(active_only: bool = True, db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    query = select(Student).where(Student.id.in_(owned_student_ids(user))).order_by(Student.full_name)
    if active_only:
        query = query.where(Student.is_active.is_(True))
    return await _students_out(db, list((await db.execute(query)).scalars().all()))


@router.get("/students/{student_id}", response_model=TeacherStudentDetail)
async def teacher_student(student_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_teacher)):
    student = await student_or_404(db, user, student_id)
    base = (await _students_out(db, [student]))[0]
    slots = (
        await db.execute(
            select(StudentScheduleSlot)
            .where(StudentScheduleSlot.student_id == student.id, StudentScheduleSlot.is_active.is_(True))
            .order_by(StudentScheduleSlot.day_of_week, StudentScheduleSlot.start_time)
        )
    ).scalars().all()
    return TeacherStudentDetail(
        **base.model_dump(),
        schedule=[
            TeacherScheduleSlot(
                day_of_week=slot.day_of_week,
                start_time=slot.start_time,
                duration_minutes=slot.duration_minutes,
                valid_from=slot.valid_from,
                valid_until=slot.valid_until,
            )
            for slot in slots
        ],
    )


@router.get("/lessons", response_model=List[TeacherLesson])
async def teacher_lessons(
    date_from: Optional[date] = Query(default=None, alias="from"),
    date_to: Optional[date] = Query(default=None, alias="to"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_teacher),
):
    """Lessons of the teacher in [from, to] (dates, Asia/Almaty; default: today + 6 days, at most 62 days):
    lessons of own students (lessons.teacher_id empty -> by students.teacher_id) plus substitutions
    (lessons.teacher_id = me). For a substitution only the student's name and course are returned."""
    from routers.lessons import LOCAL_TZ, _ensure_schedule_lessons
    from core.lesson_service import complete_overdue_lessons

    start = date_from or datetime.now(LOCAL_TZ).date()
    end = date_to or (start + timedelta(days=6))
    if end < start:
        raise HTTPException(400, "to must be on or after from")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(400, f"The range is limited to {MAX_RANGE_DAYS} days")

    # The same calendar upkeep the admin calendar does (generate lessons from the schedule, the system marks
    # finished lessons as held). The teacher changes nothing himself.
    await complete_overdue_lessons(db)
    await _ensure_schedule_lessons(db, start, end)
    await db.commit()

    lower = datetime.combine(start, time.min, tzinfo=LOCAL_TZ)
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    query = (
        select(Lesson, Event.start_time, Event.end_time, Student.full_name, Student.course, Student.teacher_id)
        .join(Event, Event.id == Lesson.event_id)
        .join(Student, Student.id == Lesson.student_id)
        .where(Event.start_time >= lower, Event.start_time < upper)
        .order_by(Event.start_time)
    )
    admin = is_admin(user)
    if not admin:
        query = query.where(effective_teacher_sql() == user.id)
    out = []
    for lesson, start_time, end_time, name, course, permanent_teacher in (await db.execute(query)).all():
        substitute = (not admin) and lesson.teacher_id == user.id and permanent_teacher != user.id
        out.append(
            TeacherLesson(
                id=lesson.id,
                is_substitute=substitute,
                student_id=None if substitute else lesson.student_id,
                student_name=name,
                course=course,
                start_time=start_time,
                end_time=end_time,
                status=lesson.status,
                topic=None if substitute else lesson.topic,
                lesson_type=lesson.lesson_type,
            )
        )
    return out
