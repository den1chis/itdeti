"""LMS catalog API (teacher/admin only): courses -> modules -> lessons -> materials/tasks,
student enrollments, lesson progress and private teacher notes.

Everything lives under the /lms prefix, so it cannot collide with existing routes."""
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.course_sync import safe_plan_dates, safe_sync_course, safe_sync_student
from core.dependencies import teacher_or_admin
from core.lms_utils import get_embed_url
from models.lms import (
    LmsCourse,
    LmsEnrollment,
    LmsLesson,
    LmsLessonProgress,
    LmsMaterial,
    LmsModule,
    LmsStudentNote,
    LmsTask,
)
from models.student import Student
from schemas.lms import (
    CourseCreate,
    CourseResponse,
    CourseTree,
    CourseUpdate,
    EnrollmentCreate,
    EnrollmentResponse,
    EnrollmentUpdate,
    LessonBrief,
    LessonCreate,
    LessonDetail,
    LessonResponse,
    LessonUpdate,
    MaterialCreate,
    MaterialResponse,
    MaterialUpdate,
    ModuleCreate,
    ModuleResponse,
    ModuleTree,
    ModuleUpdate,
    NextLesson,
    NoteCreate,
    NoteResponse,
    NoteUpdate,
    ProgressResponse,
    ProgressUpdate,
    ReorderRequest,
    ScheduleSlotBrief,
    StudentCourseProgress,
    StudentCourseSet,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
)

router = APIRouter(prefix="/lms", tags=["lms"], dependencies=[Depends(teacher_or_admin)])


# ---------------------------------------------------------------- helpers
def _apply_changes(obj, changes: dict, required: set) -> None:
    """Apply a PATCH payload (only fields the client really sent). Required columns cannot be set to null."""
    for field, value in changes.items():
        if value is None and field in required:
            raise HTTPException(400, f"{field} cannot be null")
        setattr(obj, field, value)


async def _next_order(db: AsyncSession, model, parent_col=None, parent_id=None) -> int:
    query = select(func.coalesce(func.max(model.sort_order), -1))
    if parent_col is not None:
        query = query.where(parent_col == parent_id)
    return int(await db.scalar(query)) + 1


async def _reorder(db: AsyncSession, model, parent_col, parent_id, ids: List[UUID]) -> None:
    rows = (await db.execute(select(model).where(parent_col == parent_id))).scalars().all()
    by_id = {row.id: row for row in rows}
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise HTTPException(400, "ids must list every sibling exactly once")
    for index, item_id in enumerate(ids):
        by_id[item_id].sort_order = index


async def _get_course(db: AsyncSession, course_id: UUID) -> LmsCourse:
    course = await db.scalar(select(LmsCourse).where(LmsCourse.id == course_id))
    if not course:
        raise HTTPException(404, "Course not found")
    return course


async def _get_module(db: AsyncSession, module_id: UUID) -> LmsModule:
    module = await db.scalar(select(LmsModule).where(LmsModule.id == module_id))
    if not module:
        raise HTTPException(404, "Module not found")
    return module


async def _get_lesson(db: AsyncSession, lesson_id: UUID) -> LmsLesson:
    lesson = await db.scalar(select(LmsLesson).where(LmsLesson.id == lesson_id))
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    return lesson


async def _get_student(db: AsyncSession, student_id: UUID) -> Student:
    student = await db.scalar(select(Student).where(Student.id == student_id))
    if not student:
        raise HTTPException(404, "Student not found")
    return student


async def _course_counts(db: AsyncSession, course_ids: List[UUID]) -> Dict[UUID, tuple]:
    if not course_ids:
        return {}
    rows = await db.execute(
        select(
            LmsModule.course_id,
            func.count(func.distinct(LmsModule.id)),
            func.count(func.distinct(LmsLesson.id)),
        )
        .select_from(LmsModule)
        .outerjoin(LmsLesson, LmsLesson.module_id == LmsModule.id)
        .where(LmsModule.course_id.in_(course_ids))
        .group_by(LmsModule.course_id)
    )
    counts: Dict[UUID, tuple] = {course_id: (modules, lessons, 0) for course_id, modules, lessons in rows.all()}
    student_rows = await db.execute(
        select(LmsEnrollment.course_id, func.count())
        .where(LmsEnrollment.course_id.in_(course_ids))
        .group_by(LmsEnrollment.course_id)
    )
    for course_id, students in student_rows.all():
        modules, lessons, _ = counts.get(course_id, (0, 0, 0))
        counts[course_id] = (modules, lessons, students)
    return counts


def _course_response(course: LmsCourse, counts: Dict[UUID, tuple]) -> CourseResponse:
    modules, lessons, students = counts.get(course.id, (0, 0, 0))
    return CourseResponse.model_validate(course).model_copy(
        update={"modules_count": modules, "lessons_count": lessons, "students_count": students}
    )


async def _sync_student_course(db: AsyncSession, student_id: UUID) -> None:
    """Держит старую текстовую строку students.course в соответствии с реальным назначением курса,
    чтобы таблица «Ученики» и календарь показывали название назначенного курса."""
    title = await db.scalar(
        select(LmsCourse.title)
        .join(LmsEnrollment, LmsEnrollment.course_id == LmsCourse.id)
        .where(LmsEnrollment.student_id == student_id)
        .order_by(LmsEnrollment.started_at.desc())
        .limit(1)
    )
    student = await db.scalar(select(Student).where(Student.id == student_id))
    if student:
        student.course = title


async def _build_tree(
    db: AsyncSession,
    course_id: UUID,
    statuses: Optional[Dict[UUID, str]] = None,
    dates: Optional[Dict[UUID, Tuple[datetime, Optional[str]]]] = None,
) -> List[ModuleTree]:
    modules = (
        await db.execute(
            select(LmsModule).where(LmsModule.course_id == course_id).order_by(LmsModule.sort_order, LmsModule.created_at)
        )
    ).scalars().all()
    module_ids = [module.id for module in modules]
    lessons: List[LmsLesson] = []
    if module_ids:
        lessons = (
            await db.execute(
                select(LmsLesson).where(LmsLesson.module_id.in_(module_ids)).order_by(LmsLesson.sort_order, LmsLesson.created_at)
            )
        ).scalars().all()
    lesson_ids = [lesson.id for lesson in lessons]
    material_counts: Dict[UUID, int] = {}
    task_counts: Dict[UUID, int] = {}
    if lesson_ids:
        rows = await db.execute(
            select(LmsMaterial.lesson_id, func.count()).where(LmsMaterial.lesson_id.in_(lesson_ids)).group_by(LmsMaterial.lesson_id)
        )
        material_counts = {lesson_id: count for lesson_id, count in rows.all()}
        rows = await db.execute(
            select(LmsTask.lesson_id, func.count()).where(LmsTask.lesson_id.in_(lesson_ids)).group_by(LmsTask.lesson_id)
        )
        task_counts = {lesson_id: count for lesson_id, count in rows.all()}

    by_module: Dict[UUID, List[LessonBrief]] = {module.id: [] for module in modules}
    for lesson in lessons:
        by_module[lesson.module_id].append(
            LessonBrief(
                id=lesson.id,
                title=lesson.title,
                sort_order=lesson.sort_order,
                duration_min=lesson.duration_min,
                materials_count=material_counts.get(lesson.id, 0),
                tasks_count=task_counts.get(lesson.id, 0),
                status=(statuses.get(lesson.id, "not_started") if statuses is not None else None),
                scheduled_at=(dates[lesson.id][0] if dates and lesson.id in dates else None),
                calendar_status=(dates[lesson.id][1] if dates and lesson.id in dates else None),
            )
        )
    return [
        ModuleTree(id=module.id, title=module.title, description=module.description, sort_order=module.sort_order, lessons=by_module[module.id])
        for module in modules
    ]


async def _course_lessons_ordered(db: AsyncSession, course_id: UUID) -> List[LmsLesson]:
    result = await db.execute(
        select(LmsLesson)
        .join(LmsModule, LmsModule.id == LmsLesson.module_id)
        .where(LmsModule.course_id == course_id)
        .order_by(LmsModule.sort_order, LmsModule.created_at, LmsLesson.sort_order, LmsLesson.created_at)
    )
    return list(result.scalars().all())


async def _student_statuses(db: AsyncSession, student_id: UUID, lesson_ids: List[UUID]) -> Dict[UUID, str]:
    if not lesson_ids:
        return {}
    rows = await db.execute(
        select(LmsLessonProgress.lesson_id, LmsLessonProgress.status).where(
            LmsLessonProgress.student_id == student_id, LmsLessonProgress.lesson_id.in_(lesson_ids)
        )
    )
    return {lesson_id: status for lesson_id, status in rows.all()}


async def _enrollment_response(db: AsyncSession, enrollment: LmsEnrollment, course: LmsCourse) -> EnrollmentResponse:
    lessons = await _course_lessons_ordered(db, course.id)
    statuses = await _student_statuses(db, enrollment.student_id, [lesson.id for lesson in lessons])
    done = sum(1 for lesson in lessons if statuses.get(lesson.id) == "done")
    next_index = next((index for index, lesson in enumerate(lessons) if statuses.get(lesson.id) != "done"), None)
    plan, dates = await safe_plan_dates(db, enrollment.student_id, enrollment.started_at, len(lessons))
    next_lesson = None
    if next_index is not None:
        when = dates[next_index][0] if next_index < len(dates) else None
        next_lesson = NextLesson(id=lessons[next_index].id, title=lessons[next_index].title, scheduled_at=when)
    slots = sorted(plan.active_slots, key=lambda slot: (slot.day_of_week, slot.start_time)) if plan else []
    return EnrollmentResponse(
        id=enrollment.id,
        student_id=enrollment.student_id,
        course_id=course.id,
        course_title=course.title,
        course_type=course.course_type,
        status=enrollment.status,
        started_at=enrollment.started_at,
        lessons_total=len(lessons),
        lessons_done=done,
        next_lesson=next_lesson,
        schedule=[
            ScheduleSlotBrief(
                day_of_week=slot.day_of_week,
                start_time=slot.start_time,
                duration_minutes=slot.duration_minutes,
                valid_from=slot.valid_from,
            )
            for slot in slots
        ],
        first_lesson_at=dates[0][0] if dates else None,
        last_lesson_at=dates[-1][0] if dates and plan is not None and plan.end is not None else None,
        lessons_scheduled=len(dates),
    )


def _material_out(material: LmsMaterial) -> MaterialResponse:
    return MaterialResponse(
        id=material.id,
        lesson_id=material.lesson_id,
        kind=material.kind,
        title=material.title,
        url=material.url,
        embed_url=get_embed_url(material.url) if material.kind == "link" else None,
        body=material.body,
        sort_order=material.sort_order,
        created_at=material.created_at,
        updated_at=material.updated_at,
    )


def _check_material_content(material: LmsMaterial) -> None:
    if material.kind == "link" and not (material.url and material.url.strip()):
        raise HTTPException(400, "url is required for a link material")
    if material.kind == "text" and not (material.body and material.body.strip()):
        raise HTTPException(400, "body is required for a text material")


# ---------------------------------------------------------------- courses
@router.get("/courses", response_model=List[CourseResponse])
async def list_courses(include_archived: bool = False, db: AsyncSession = Depends(get_db)):
    query = select(LmsCourse).order_by(LmsCourse.sort_order, LmsCourse.created_at)
    if not include_archived:
        query = query.where(LmsCourse.is_archived.is_(False))
    courses = (await db.execute(query)).scalars().all()
    counts = await _course_counts(db, [course.id for course in courses])
    return [_course_response(course, counts) for course in courses]


@router.post("/courses", response_model=CourseResponse, status_code=201)
async def create_course(payload: CourseCreate, db: AsyncSession = Depends(get_db)):
    course = LmsCourse(**payload.model_dump(), sort_order=await _next_order(db, LmsCourse))
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return _course_response(course, {})


@router.get("/courses/{course_id}", response_model=CourseTree)
async def get_course(course_id: UUID, db: AsyncSession = Depends(get_db)):
    course = await _get_course(db, course_id)
    modules = await _build_tree(db, course.id)
    base = _course_response(course, await _course_counts(db, [course.id]))
    return CourseTree(**base.model_dump(), modules=modules)


@router.patch("/courses/{course_id}", response_model=CourseResponse)
async def update_course(course_id: UUID, payload: CourseUpdate, db: AsyncSession = Depends(get_db)):
    course = await _get_course(db, course_id)
    old_title = course.title
    _apply_changes(course, payload.model_dump(exclude_unset=True), {"title", "course_type", "is_archived", "sort_order"})
    if course.title != old_title:
        enrolled = (await db.execute(select(LmsEnrollment.student_id).where(LmsEnrollment.course_id == course.id))).scalars().all()
        for student_id in enrolled:
            student = await db.scalar(select(Student).where(Student.id == student_id))
            if student:
                student.course = course.title
    await db.commit()
    await db.refresh(course)
    return _course_response(course, await _course_counts(db, [course.id]))


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(course_id: UUID, db: AsyncSession = Depends(get_db)):
    course = await _get_course(db, course_id)
    enrolled = await db.scalar(select(func.count()).select_from(LmsEnrollment).where(LmsEnrollment.course_id == course_id))
    if enrolled:
        raise HTTPException(409, "На курсе есть ученики, удалить его нельзя. Архивируйте курс или сначала снимите его с учеников.")
    await db.delete(course)
    await db.commit()


# ---------------------------------------------------------------- modules
@router.post("/courses/{course_id}/modules", response_model=ModuleResponse, status_code=201)
async def create_module(course_id: UUID, payload: ModuleCreate, db: AsyncSession = Depends(get_db)):
    await _get_course(db, course_id)
    module = LmsModule(course_id=course_id, sort_order=await _next_order(db, LmsModule, LmsModule.course_id, course_id), **payload.model_dump())
    db.add(module)
    await db.commit()
    await db.refresh(module)
    return module


@router.post("/courses/{course_id}/modules/reorder", status_code=204)
async def reorder_modules(course_id: UUID, payload: ReorderRequest, db: AsyncSession = Depends(get_db)):
    await _get_course(db, course_id)
    await _reorder(db, LmsModule, LmsModule.course_id, course_id, payload.ids)
    await db.commit()


@router.patch("/modules/{module_id}", response_model=ModuleResponse)
async def update_module(module_id: UUID, payload: ModuleUpdate, db: AsyncSession = Depends(get_db)):
    module = await _get_module(db, module_id)
    _apply_changes(module, payload.model_dump(exclude_unset=True), {"title", "sort_order"})
    await db.commit()
    await db.refresh(module)
    return module


@router.delete("/modules/{module_id}", status_code=204)
async def delete_module(module_id: UUID, db: AsyncSession = Depends(get_db)):
    module = await _get_module(db, module_id)
    course_id = module.course_id
    await db.delete(module)
    await db.flush()
    await safe_sync_course(db, course_id)
    await db.commit()


# ---------------------------------------------------------------- lessons
@router.post("/modules/{module_id}/lessons", response_model=LessonResponse, status_code=201)
async def create_lesson(module_id: UUID, payload: LessonCreate, db: AsyncSession = Depends(get_db)):
    module = await _get_module(db, module_id)
    lesson = LmsLesson(module_id=module_id, sort_order=await _next_order(db, LmsLesson, LmsLesson.module_id, module_id), **payload.model_dump())
    db.add(lesson)
    await db.flush()
    await safe_sync_course(db, module.course_id)  # число уроков курса выросло: расписание учеников удлиняется
    await db.commit()
    await db.refresh(lesson)
    return lesson


@router.post("/modules/{module_id}/lessons/reorder", status_code=204)
async def reorder_lessons(module_id: UUID, payload: ReorderRequest, db: AsyncSession = Depends(get_db)):
    await _get_module(db, module_id)
    await _reorder(db, LmsLesson, LmsLesson.module_id, module_id, payload.ids)
    await db.commit()


@router.get("/lessons/{lesson_id}", response_model=LessonDetail)
async def get_lesson(lesson_id: UUID, db: AsyncSession = Depends(get_db)):
    lesson = await _get_lesson(db, lesson_id)
    module = await _get_module(db, lesson.module_id)
    course = await _get_course(db, module.course_id)
    materials = (
        await db.execute(select(LmsMaterial).where(LmsMaterial.lesson_id == lesson_id).order_by(LmsMaterial.sort_order, LmsMaterial.created_at))
    ).scalars().all()
    tasks = (
        await db.execute(select(LmsTask).where(LmsTask.lesson_id == lesson_id).order_by(LmsTask.sort_order, LmsTask.created_at))
    ).scalars().all()
    return LessonDetail(
        **LessonResponse.model_validate(lesson).model_dump(),
        course_id=course.id,
        course_title=course.title,
        module_title=module.title,
        materials=[_material_out(material) for material in materials],
        tasks=[TaskResponse.model_validate(task) for task in tasks],
    )


@router.patch("/lessons/{lesson_id}", response_model=LessonResponse)
async def update_lesson(lesson_id: UUID, payload: LessonUpdate, db: AsyncSession = Depends(get_db)):
    lesson = await _get_lesson(db, lesson_id)
    changes = payload.model_dump(exclude_unset=True)
    new_module_id = changes.pop("module_id", None)
    if new_module_id and new_module_id != lesson.module_id:
        source = await _get_module(db, lesson.module_id)
        target = await _get_module(db, new_module_id)
        if source.course_id != target.course_id:
            raise HTTPException(400, "A lesson can only be moved within its course")
        lesson.module_id = target.id
        if "sort_order" not in changes:
            lesson.sort_order = await _next_order(db, LmsLesson, LmsLesson.module_id, target.id)
    _apply_changes(lesson, changes, {"title", "sort_order"})
    await db.commit()
    await db.refresh(lesson)
    return lesson


@router.delete("/lessons/{lesson_id}", status_code=204)
async def delete_lesson(lesson_id: UUID, db: AsyncSession = Depends(get_db)):
    lesson = await _get_lesson(db, lesson_id)
    module = await _get_module(db, lesson.module_id)
    await db.delete(lesson)
    await db.flush()
    await safe_sync_course(db, module.course_id)
    await db.commit()


# ---------------------------------------------------------------- materials
@router.post("/lessons/{lesson_id}/materials", response_model=MaterialResponse, status_code=201)
async def create_material(lesson_id: UUID, payload: MaterialCreate, db: AsyncSession = Depends(get_db)):
    await _get_lesson(db, lesson_id)
    material = LmsMaterial(lesson_id=lesson_id, sort_order=await _next_order(db, LmsMaterial, LmsMaterial.lesson_id, lesson_id), **payload.model_dump())
    _check_material_content(material)
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return _material_out(material)


@router.patch("/materials/{material_id}", response_model=MaterialResponse)
async def update_material(material_id: UUID, payload: MaterialUpdate, db: AsyncSession = Depends(get_db)):
    material = await db.scalar(select(LmsMaterial).where(LmsMaterial.id == material_id))
    if not material:
        raise HTTPException(404, "Material not found")
    _apply_changes(material, payload.model_dump(exclude_unset=True), {"title", "sort_order"})
    _check_material_content(material)
    await db.commit()
    await db.refresh(material)
    return _material_out(material)


@router.delete("/materials/{material_id}", status_code=204)
async def delete_material(material_id: UUID, db: AsyncSession = Depends(get_db)):
    material = await db.scalar(select(LmsMaterial).where(LmsMaterial.id == material_id))
    if not material:
        raise HTTPException(404, "Material not found")
    await db.delete(material)
    await db.commit()


# ---------------------------------------------------------------- tasks
@router.post("/lessons/{lesson_id}/tasks", response_model=TaskResponse, status_code=201)
async def create_task(lesson_id: UUID, payload: TaskCreate, db: AsyncSession = Depends(get_db)):
    await _get_lesson(db, lesson_id)
    task = LmsTask(lesson_id=lesson_id, sort_order=await _next_order(db, LmsTask, LmsTask.lesson_id, lesson_id), **payload.model_dump())
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: UUID, payload: TaskUpdate, db: AsyncSession = Depends(get_db)):
    task = await db.scalar(select(LmsTask).where(LmsTask.id == task_id))
    if not task:
        raise HTTPException(404, "Task not found")
    _apply_changes(task, payload.model_dump(exclude_unset=True), {"title", "kind", "sort_order"})
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: UUID, db: AsyncSession = Depends(get_db)):
    task = await db.scalar(select(LmsTask).where(LmsTask.id == task_id))
    if not task:
        raise HTTPException(404, "Task not found")
    await db.delete(task)
    await db.commit()


# ---------------------------------------------------------------- enrollments
@router.get("/students/{student_id}/enrollments", response_model=List[EnrollmentResponse])
async def list_student_enrollments(student_id: UUID, db: AsyncSession = Depends(get_db)):
    await _get_student(db, student_id)
    rows = await db.execute(
        select(LmsEnrollment, LmsCourse)
        .join(LmsCourse, LmsCourse.id == LmsEnrollment.course_id)
        .where(LmsEnrollment.student_id == student_id)
        .order_by(LmsEnrollment.started_at)
    )
    return [await _enrollment_response(db, enrollment, course) for enrollment, course in rows.all()]


@router.post("/students/{student_id}/enrollments", response_model=EnrollmentResponse, status_code=201)
async def enroll_student(student_id: UUID, payload: EnrollmentCreate, db: AsyncSession = Depends(get_db)):
    await _get_student(db, student_id)
    course = await _get_course(db, payload.course_id)
    if course.is_archived:
        raise HTTPException(400, "Курс в архиве, его нельзя назначить")
    existing = await db.scalar(select(LmsEnrollment.id).where(LmsEnrollment.student_id == student_id))
    if existing:
        raise HTTPException(409, "У ученика уже есть курс. Сначала снимите его или замените курс.")
    enrollment = LmsEnrollment(student_id=student_id, course_id=course.id)
    db.add(enrollment)
    try:
        await db.flush()
        await _sync_student_course(db, student_id)
        await safe_sync_student(db, student_id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "У ученика уже есть курс. Сначала снимите его или замените курс.") from exc
    await db.refresh(enrollment)
    return await _enrollment_response(db, enrollment, course)


@router.put("/students/{student_id}/course", response_model=Optional[EnrollmentResponse])
async def set_student_course(student_id: UUID, payload: StudentCourseSet, db: AsyncSession = Depends(get_db)):
    """Заменить курс ученика одним действием (для карточки ученика): прежнее назначение снимается,
    новое создаётся. course_id = null просто снимает курс. Тот же курс, что уже назначен, не пересоздаётся."""
    await _get_student(db, student_id)
    current = (await db.execute(select(LmsEnrollment).where(LmsEnrollment.student_id == student_id))).scalars().all()
    if payload.course_id is None:
        for enrollment in current:
            await db.delete(enrollment)
        await db.flush()
        await _sync_student_course(db, student_id)
        await safe_sync_student(db, student_id)
        await db.commit()
        return None
    course = await _get_course(db, payload.course_id)
    same = next((enrollment for enrollment in current if enrollment.course_id == course.id), None)
    if same:
        for enrollment in current:
            if enrollment is not same:
                await db.delete(enrollment)
        await db.flush()
        await _sync_student_course(db, student_id)
        await safe_sync_student(db, student_id)
        await db.commit()
        await db.refresh(same)
        return await _enrollment_response(db, same, course)
    if course.is_archived:
        raise HTTPException(400, "Курс в архиве, его нельзя назначить")
    for enrollment in current:
        await db.delete(enrollment)
    await db.flush()
    enrollment = LmsEnrollment(student_id=student_id, course_id=course.id)
    db.add(enrollment)
    await db.flush()
    await _sync_student_course(db, student_id)
    await safe_sync_student(db, student_id)
    await db.commit()
    await db.refresh(enrollment)
    return await _enrollment_response(db, enrollment, course)


@router.patch("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def update_enrollment(enrollment_id: UUID, payload: EnrollmentUpdate, db: AsyncSession = Depends(get_db)):
    enrollment = await db.scalar(select(LmsEnrollment).where(LmsEnrollment.id == enrollment_id))
    if not enrollment:
        raise HTTPException(404, "Enrollment not found")
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is None:
        raise HTTPException(400, "status cannot be null")
    for field, value in changes.items():
        setattr(enrollment, field, value)
    await db.flush()
    if "status" in changes:
        await safe_sync_student(db, enrollment.student_id)
    await db.commit()
    await db.refresh(enrollment)
    course = await _get_course(db, enrollment.course_id)
    return await _enrollment_response(db, enrollment, course)


@router.delete("/enrollments/{enrollment_id}", status_code=204)
async def delete_enrollment(enrollment_id: UUID, db: AsyncSession = Depends(get_db)):
    """Unassign a course. The student's progress records are kept, so re-assigning restores them."""
    enrollment = await db.scalar(select(LmsEnrollment).where(LmsEnrollment.id == enrollment_id))
    if not enrollment:
        raise HTTPException(404, "Enrollment not found")
    student_id = enrollment.student_id
    await db.delete(enrollment)
    await db.flush()
    await _sync_student_course(db, student_id)
    await safe_sync_student(db, student_id)
    await db.commit()


# ---------------------------------------------------------------- progress
@router.get("/students/{student_id}/courses/{course_id}/progress", response_model=StudentCourseProgress)
async def student_course_progress(student_id: UUID, course_id: UUID, db: AsyncSession = Depends(get_db)):
    await _get_student(db, student_id)
    course = await _get_course(db, course_id)
    lessons = await _course_lessons_ordered(db, course_id)
    statuses = await _student_statuses(db, student_id, [lesson.id for lesson in lessons])
    started_at = await db.scalar(
        select(LmsEnrollment.started_at).where(LmsEnrollment.student_id == student_id, LmsEnrollment.course_id == course_id)
    )
    dates: Dict[UUID, Tuple[datetime, Optional[str]]] = {}
    if started_at is not None:
        _, plan_dates = await safe_plan_dates(db, student_id, started_at, len(lessons))
        dates = {lesson.id: when for lesson, when in zip(lessons, plan_dates)}
    modules = await _build_tree(db, course_id, statuses, dates)
    return StudentCourseProgress(
        student_id=student_id,
        course_id=course_id,
        course_title=course.title,
        lessons_total=len(lessons),
        lessons_done=sum(1 for lesson in lessons if statuses.get(lesson.id) == "done"),
        modules=modules,
    )


@router.put("/students/{student_id}/lessons/{lesson_id}/progress", response_model=ProgressResponse)
async def set_lesson_progress(student_id: UUID, lesson_id: UUID, payload: ProgressUpdate, db: AsyncSession = Depends(get_db)):
    await _get_student(db, student_id)
    lesson = await _get_lesson(db, lesson_id)
    module = await _get_module(db, lesson.module_id)
    enrolled = await db.scalar(
        select(LmsEnrollment.id).where(LmsEnrollment.student_id == student_id, LmsEnrollment.course_id == module.course_id)
    )
    if not enrolled:
        raise HTTPException(400, "Student is not enrolled in this course")
    progress = await db.scalar(
        select(LmsLessonProgress).where(LmsLessonProgress.student_id == student_id, LmsLessonProgress.lesson_id == lesson_id)
    )
    if progress:
        progress.status = payload.status
    else:
        progress = LmsLessonProgress(student_id=student_id, lesson_id=lesson_id, status=payload.status)
        db.add(progress)
    await db.commit()
    await db.refresh(progress)
    return ProgressResponse(student_id=student_id, lesson_id=lesson_id, status=progress.status, updated_at=progress.updated_at)


# ---------------------------------------------------------------- notes (visible to teacher/admin only)
@router.get("/students/{student_id}/notes", response_model=List[NoteResponse])
async def list_notes(student_id: UUID, lesson_id: Optional[UUID] = None, db: AsyncSession = Depends(get_db)):
    await _get_student(db, student_id)
    query = select(LmsStudentNote).where(LmsStudentNote.student_id == student_id)
    if lesson_id:
        query = query.where(LmsStudentNote.lesson_id == lesson_id)
    result = await db.execute(query.order_by(LmsStudentNote.created_at.desc()))
    return result.scalars().all()


@router.post("/students/{student_id}/notes", response_model=NoteResponse, status_code=201)
async def create_note(student_id: UUID, payload: NoteCreate, db: AsyncSession = Depends(get_db)):
    await _get_student(db, student_id)
    if payload.lesson_id:
        await _get_lesson(db, payload.lesson_id)
    note = LmsStudentNote(student_id=student_id, **payload.model_dump())
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.patch("/notes/{note_id}", response_model=NoteResponse)
async def update_note(note_id: UUID, payload: NoteUpdate, db: AsyncSession = Depends(get_db)):
    note = await db.scalar(select(LmsStudentNote).where(LmsStudentNote.id == note_id))
    if not note:
        raise HTTPException(404, "Note not found")
    note.content = payload.content
    await db.commit()
    await db.refresh(note)
    return note


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: UUID, db: AsyncSession = Depends(get_db)):
    note = await db.scalar(select(LmsStudentNote).where(LmsStudentNote.id == note_id))
    if not note:
        raise HTTPException(404, "Note not found")
    await db.delete(note)
    await db.commit()
