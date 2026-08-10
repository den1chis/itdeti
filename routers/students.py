from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List, Optional
from core.database import get_db
from core.dependencies import get_current_user, admin_only
from models.student import Student
from models.parent import Parent, StudentParent
from models.course import Course
from models.user import User
from schemas.students import (
    StudentCreate, StudentUpdate, StudentResponse,
    ParentCreate, ParentUpdate, ParentResponse,
    CourseCreate, CourseResponse,
)
import uuid

router = APIRouter(tags=["students"])


# ══════════════════════════════════════════
# COURSES
# ══════════════════════════════════════════

@router.post("/courses", response_model=CourseResponse)
async def create_course(
    payload: CourseCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_only),
):
    course = Course(**payload.model_dump())
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


@router.get("/courses", response_model=List[CourseResponse])
async def list_courses(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Course).where(Course.is_active == True))
    return result.scalars().all()


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_only),
):
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    course.is_active = False
    await db.commit()


# ══════════════════════════════════════════
# PARENTS
# ══════════════════════════════════════════

@router.post("/parents", response_model=ParentResponse)
async def create_parent(
    payload: ParentCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    parent = Parent(**payload.model_dump())
    db.add(parent)
    await db.commit()
    await db.refresh(parent)
    return parent


@router.get("/parents", response_model=List[ParentResponse])
async def list_parents(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Parent))
    return result.scalars().all()


@router.get("/parents/{parent_id}", response_model=ParentResponse)
async def get_parent(
    parent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Parent).where(Parent.id == parent_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    return parent


@router.patch("/parents/{parent_id}", response_model=ParentResponse)
async def update_parent(
    parent_id: uuid.UUID,
    payload: ParentUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Parent).where(Parent.id == parent_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(parent, field, value)
    await db.commit()
    await db.refresh(parent)
    return parent


@router.delete("/parents/{parent_id}", status_code=204)
async def delete_parent(
    parent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_only),
):
    result = await db.execute(select(Parent).where(Parent.id == parent_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    await db.delete(parent)
    await db.commit()


# ══════════════════════════════════════════
# STUDENTS
# ══════════════════════════════════════════

async def _load_parents(db: AsyncSession, student_id: uuid.UUID) -> List[Parent]:
    result = await db.execute(
        select(Parent)
        .join(StudentParent, StudentParent.parent_id == Parent.id)
        .where(StudentParent.student_id == student_id)
    )
    return result.scalars().all()


@router.post("/students", response_model=StudentResponse, status_code=201)
async def create_student(
    payload: StudentCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    data = payload.model_dump(exclude={"parent_ids"})
    student = Student(**data)
    db.add(student)
    await db.flush()

    for parent_id in (payload.parent_ids or []):
        res = await db.execute(select(Parent).where(Parent.id == parent_id))
        if not res.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"Parent {parent_id} not found")
        db.add(StudentParent(student_id=student.id, parent_id=parent_id))

    await db.commit()
    await db.refresh(student)
    student.parents = await _load_parents(db, student.id)
    return student


@router.get("/students", response_model=List[StudentResponse])
async def list_students(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Student)
    if active_only:
        q = q.where(Student.is_active == True)
    result = await db.execute(q.order_by(Student.full_name))
    students = result.scalars().all()
    for s in students:
        s.parents = await _load_parents(db, s.id)
    return students


@router.get("/students/{student_id}", response_model=StudentResponse)
async def get_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student.parents = await _load_parents(db, student.id)
    return student


@router.patch("/students/{student_id}", response_model=StudentResponse)
async def update_student(
    student_id: uuid.UUID,
    payload: StudentUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(student, field, value)
    await db.commit()
    await db.refresh(student)
    student.parents = await _load_parents(db, student.id)
    return student


@router.delete("/students/{student_id}", status_code=204)
async def deactivate_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_only),
):
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student.is_active = False
    await db.commit()


# ── Привязка родителя к ученику ──────────────────────────────
@router.post("/students/{student_id}/parents/{parent_id}", status_code=201)
async def link_parent(
    student_id: uuid.UUID,
    parent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    res = await db.execute(select(StudentParent).where(
        StudentParent.student_id == student_id,
        StudentParent.parent_id == parent_id,
    ))
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already linked")
    db.add(StudentParent(student_id=student_id, parent_id=parent_id))
    await db.commit()
    return {"detail": "Parent linked"}


@router.delete("/students/{student_id}/parents/{parent_id}", status_code=204)
async def unlink_parent(
    student_id: uuid.UUID,
    parent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await db.execute(delete(StudentParent).where(
        StudentParent.student_id == student_id,
        StudentParent.parent_id == parent_id,
    ))
    await db.commit()
