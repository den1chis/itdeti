from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.dependencies import admin_only, get_current_user, teacher_or_admin
from models.lesson import Lesson
from models.parent import Parent, StudentParent
from models.payment import Payment
from models.schedule import Event, StudentScheduleSlot
from models.student import Student
from models.user import User
from schemas.schedule import ScheduleSlotCreate, ScheduleSlotResponse, ScheduleSlotUpdate
from schemas.students import BalanceResponse, ParentCreate, ParentResponse, ParentUpdate, StudentCreate, StudentResponse, StudentUpdate

router = APIRouter(tags=["students"])


def _period_occurrences(slot: StudentScheduleSlot, start: date, end: date) -> int:
    if end < start or not slot.is_active:
        return 0
    actual_start = max(start, slot.valid_from)
    actual_end = min(end, slot.valid_until) if slot.valid_until else end
    if actual_end < actual_start:
        return 0
    first = actual_start + timedelta(days=(slot.day_of_week - actual_start.weekday()) % 7)
    if first > actual_end:
        return 0
    return ((actual_end - first).days // 7) + 1


async def _month_stats(db: AsyncSession, student: Student, year: int, month: int):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    result = await db.execute(select(StudentScheduleSlot).where(StudentScheduleSlot.student_id == student.id))
    count = sum(_period_occurrences(slot, start, end) for slot in result.scalars().all())
    return count, Decimal(count) * Decimal(student.lesson_price)


async def _parents_for_student(db: AsyncSession, student_id: UUID) -> list[Parent]:
    result = await db.execute(
        select(Parent)
        .join(StudentParent, StudentParent.parent_id == Parent.id)
        .where(StudentParent.student_id == student_id)
        .order_by(Parent.full_name)
    )
    return list(result.scalars().all())


async def _student_response(db: AsyncSession, student: Student) -> StudentResponse:
    today = date.today()
    current_lessons, current_due = await _month_stats(db, student, today.year, today.month)
    remaining = int(max(Decimal("0"), Decimal(student.balance)) // Decimal(student.lesson_price))
    return StudentResponse(
        id=student.id,
        user_id=student.user_id,
        full_name=student.full_name,
        birth_date=student.birth_date,
        course=student.course,
        notes=student.notes,
        balance=student.balance,
        lesson_price=student.lesson_price,
        is_active=student.is_active,
        created_at=student.created_at,
        updated_at=student.updated_at,
        remaining_lessons=remaining,
        current_month_lessons=current_lessons,
        current_month_due=current_due,
        parents=await _parents_for_student(db, student.id),
    )


@router.post("/parents", response_model=ParentResponse, status_code=201)
async def create_parent(payload: ParentCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    parent = Parent(**payload.model_dump())
    db.add(parent)
    await db.commit()
    await db.refresh(parent)
    return parent


@router.get("/parents", response_model=List[ParentResponse])
async def list_parents(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    result = await db.execute(select(Parent).order_by(Parent.full_name))
    return result.scalars().all()


@router.get("/parents/{parent_id}", response_model=ParentResponse)
async def get_parent(parent_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    parent = await db.scalar(select(Parent).where(Parent.id == parent_id))
    if not parent:
        raise HTTPException(404, "Parent not found")
    return parent


@router.patch("/parents/{parent_id}", response_model=ParentResponse)
async def update_parent(parent_id: UUID, payload: ParentUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    parent = await db.scalar(select(Parent).where(Parent.id == parent_id).with_for_update())
    if not parent:
        raise HTTPException(404, "Parent not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(parent, field, value)
    await db.commit()
    await db.refresh(parent)
    return parent


@router.post("/students", response_model=StudentResponse, status_code=201)
async def create_student(payload: StudentCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    if payload.balance < 0:
        raise HTTPException(400, "Initial balance cannot be negative")
    student = Student(**payload.model_dump(exclude={"parent_ids"}))
    db.add(student)
    await db.flush()
    for parent_id in payload.parent_ids:
        if not await db.scalar(select(Parent.id).where(Parent.id == parent_id)):
            raise HTTPException(404, f"Parent {parent_id} not found")
        db.add(StudentParent(student_id=student.id, parent_id=parent_id))
    await db.commit()
    await db.refresh(student)
    return await _student_response(db, student)


@router.get("/students", response_model=List[StudentResponse])
async def list_students(active_only: bool = True, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    query = select(Student).order_by(Student.full_name)
    if active_only:
        query = query.where(Student.is_active.is_(True))
    result = await db.execute(query)
    return [await _student_response(db, student) for student in result.scalars().all()]


@router.get("/students/{student_id}", response_model=StudentResponse)
async def get_student(student_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    student = await db.scalar(select(Student).where(Student.id == student_id))
    if not student:
        raise HTTPException(404, "Student not found")
    return await _student_response(db, student)


@router.patch("/students/{student_id}", response_model=StudentResponse)
async def update_student(student_id: UUID, payload: StudentUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    student = await db.scalar(select(Student).where(Student.id == student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")
    changes = payload.model_dump(exclude_none=True)
    if "balance" in changes and Decimal(changes["balance"]) < 0:
        raise HTTPException(400, "Balance cannot be negative")
    for field, value in changes.items():
        setattr(student, field, value)
    await db.commit()
    await db.refresh(student)
    return await _student_response(db, student)


@router.delete("/students/{student_id}", status_code=204)
async def deactivate_student(student_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(admin_only)):
    student = await db.scalar(select(Student).where(Student.id == student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")
    student.is_active = False
    await db.execute(
        StudentScheduleSlot.__table__.update()
        .where(StudentScheduleSlot.student_id == student_id)
        .values(is_active=False, valid_until=date.today())
    )
    await db.commit()


@router.post("/students/{student_id}/parents/{parent_id}", status_code=201)
async def link_parent(student_id: UUID, parent_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    if not await db.scalar(select(Student.id).where(Student.id == student_id)):
        raise HTTPException(404, "Student not found")
    if not await db.scalar(select(Parent.id).where(Parent.id == parent_id)):
        raise HTTPException(404, "Parent not found")
    if await db.scalar(select(StudentParent).where(StudentParent.student_id == student_id, StudentParent.parent_id == parent_id)):
        raise HTTPException(409, "Parent already linked")
    db.add(StudentParent(student_id=student_id, parent_id=parent_id))
    await db.commit()
    return {"detail": "Parent linked"}


@router.delete("/students/{student_id}/parents/{parent_id}", status_code=204)
async def unlink_parent(student_id: UUID, parent_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    await db.execute(delete(StudentParent).where(StudentParent.student_id == student_id, StudentParent.parent_id == parent_id))
    await db.commit()


@router.post("/students/{student_id}/schedule", response_model=ScheduleSlotResponse, status_code=201)
async def create_schedule_slot(student_id: UUID, payload: ScheduleSlotCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    if not await db.scalar(select(Student.id).where(Student.id == student_id, Student.is_active.is_(True))):
        raise HTTPException(404, "Active student not found")
    duplicate = await db.scalar(
        select(StudentScheduleSlot).where(
            StudentScheduleSlot.student_id == student_id,
            StudentScheduleSlot.day_of_week == payload.day_of_week,
            StudentScheduleSlot.start_time == payload.start_time,
            StudentScheduleSlot.valid_from == payload.valid_from,
        )
    )
    if duplicate:
        raise HTTPException(409, "This schedule entry already exists")
    slot = StudentScheduleSlot(student_id=student_id, **payload.model_dump())
    db.add(slot)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "This schedule entry already exists") from exc
    await db.refresh(slot)
    return slot


@router.get("/students/{student_id}/schedule", response_model=List[ScheduleSlotResponse])
async def list_schedule_slots(student_id: UUID, include_inactive: bool = False, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    query = select(StudentScheduleSlot).where(StudentScheduleSlot.student_id == student_id)
    if not include_inactive:
        query = query.where(StudentScheduleSlot.is_active.is_(True))
    result = await db.execute(query.order_by(StudentScheduleSlot.day_of_week, StudentScheduleSlot.start_time))
    return result.scalars().all()


@router.patch("/students/{student_id}/schedule/{slot_id}", response_model=ScheduleSlotResponse)
async def update_schedule_slot(student_id: UUID, slot_id: UUID, payload: ScheduleSlotUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    slot = await db.scalar(select(StudentScheduleSlot).where(StudentScheduleSlot.id == slot_id, StudentScheduleSlot.student_id == student_id).with_for_update())
    if not slot:
        raise HTTPException(404, "Schedule entry not found")

    changes = payload.model_dump(exclude_none=True)
    new_day = changes.get("day_of_week", slot.day_of_week)
    new_time = changes.get("start_time", slot.start_time)
    new_from = changes.get("valid_from", slot.valid_from)
    new_until = changes.get("valid_until", slot.valid_until)
    if new_until and new_until < new_from:
        raise HTTPException(400, "valid_until must be on or after valid_from")

    duplicate = await db.scalar(select(StudentScheduleSlot).where(StudentScheduleSlot.id != slot.id, StudentScheduleSlot.student_id == student_id, StudentScheduleSlot.day_of_week == new_day, StudentScheduleSlot.start_time == new_time, StudentScheduleSlot.valid_from == new_from))
    if duplicate:
        raise HTTPException(409, "This schedule entry already exists")

    future = await db.execute(
        select(Lesson, Event)
        .join(Event, Event.id == Lesson.event_id)
        .where(Lesson.schedule_slot_id == slot.id, Lesson.status == "scheduled", Lesson.original_start_time.is_not(None), Lesson.original_start_time >= datetime.now().astimezone())
    )
    for lesson, event in future.all():
        await db.delete(lesson)
        await db.delete(event)

    for field, value in changes.items():
        setattr(slot, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "This schedule entry already exists") from exc
    await db.refresh(slot)
    return slot


@router.delete("/students/{student_id}/schedule/{slot_id}", status_code=204)
async def deactivate_schedule_slot(student_id: UUID, slot_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    slot = await db.scalar(select(StudentScheduleSlot).where(StudentScheduleSlot.id == slot_id, StudentScheduleSlot.student_id == student_id).with_for_update())
    if not slot:
        raise HTTPException(404, "Schedule entry not found")
    future = await db.execute(
        select(Lesson, Event)
        .join(Event, Event.id == Lesson.event_id)
        .where(Lesson.schedule_slot_id == slot.id, Lesson.status == "scheduled", Lesson.original_start_time.is_not(None), Lesson.original_start_time >= datetime.now().astimezone())
    )
    for lesson, event in future.all():
        await db.delete(lesson)
        await db.delete(event)
    slot.is_active = False
    slot.valid_until = date.today()
    await db.commit()


@router.get("/students/{student_id}/balance", response_model=BalanceResponse)
async def student_balance(student_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    student = await db.scalar(select(Student).where(Student.id == student_id))
    if not student:
        raise HTTPException(404, "Student not found")

    now = date.today()
    month_start = date(now.year, now.month, 1)
    month_end = date(now.year, now.month, monthrange(now.year, now.month)[1])
    month_lessons, month_due = await _month_stats(db, student, now.year, now.month)
    result = await db.execute(select(Payment.amount).where(Payment.student_id == student_id, Payment.recorded_at >= datetime.combine(month_start, time.min).astimezone(), Payment.recorded_at < datetime.combine(month_end + timedelta(days=1), time.min).astimezone()))
    payments_total = sum((Decimal(v) for v in result.scalars().all()), Decimal("0"))

    return BalanceResponse(
        student_id=student.id,
        balance=student.balance,
        remaining_lessons=int(max(Decimal("0"), Decimal(student.balance)) // Decimal(student.lesson_price)),
        current_month_lessons=month_lessons,
        current_month_due=month_due,
        payments_this_month=payments_total,
        monthly_debt=max(Decimal("0"), month_due - payments_total),
    )
