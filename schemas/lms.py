from datetime import date, datetime, time
from typing import Any, Dict, List, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.lms_utils import is_http_url

CourseType = Literal["code", "no_code"]
MaterialKind = Literal["link", "text"]
TaskKind = Literal["code", "practice"]
EnrollmentStatus = Literal["active", "paused", "finished"]
ProgressStatus = Literal["not_started", "in_progress", "done"]


# ---------- courses ----------
class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    course_type: CourseType = "code"
    description: Optional[str] = None


class CourseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    course_type: Optional[CourseType] = None
    description: Optional[str] = None
    is_archived: Optional[bool] = None
    sort_order: Optional[int] = None


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    course_type: str
    description: Optional[str]
    sort_order: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    modules_count: int = 0
    lessons_count: int = 0
    students_count: int = 0  # сколько учеников сейчас назначено на курс


# ---------- modules ----------
class ModuleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class ModuleUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    sort_order: Optional[int] = None


class ModuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    description: Optional[str]
    sort_order: int


# ---------- lessons ----------
class LessonCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    duration_min: Optional[int] = Field(default=None, gt=0, le=1440)


class LessonUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    duration_min: Optional[int] = Field(default=None, gt=0, le=1440)
    sort_order: Optional[int] = None
    module_id: Optional[uuid.UUID] = None  # move a lesson to another module of the same course


class LessonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    module_id: uuid.UUID
    title: str
    description: Optional[str]
    duration_min: Optional[int]
    sort_order: int


class LessonBrief(BaseModel):
    id: uuid.UUID
    title: str
    sort_order: int
    duration_min: Optional[int] = None
    materials_count: int = 0
    tasks_count: int = 0
    status: Optional[str] = None  # filled only in per-student progress view
    scheduled_at: Optional[datetime] = None  # дата урока по расписанию ученика (только в карточке ученика)
    calendar_status: Optional[str] = None  # completed / cancelled / rescheduled, если занятие в календаре не «запланировано»
    held: Optional[bool] = None  # урок прошёл: по времени уже закончился или отмечен проведённым (только в карточке ученика)


class ModuleTree(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    sort_order: int
    lessons: List[LessonBrief] = Field(default_factory=list)


class CourseTree(CourseResponse):
    modules: List[ModuleTree] = Field(default_factory=list)


class ReorderRequest(BaseModel):
    ids: List[uuid.UUID] = Field(min_length=1)


# ---------- materials ----------
class MaterialCreate(BaseModel):
    kind: MaterialKind = "link"
    title: str = Field(min_length=1, max_length=200)
    url: Optional[str] = Field(default=None, max_length=2000)
    body: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not is_http_url(value):
            raise ValueError("url must start with http:// or https://")
        return value


class MaterialUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    url: Optional[str] = Field(default=None, max_length=2000)
    body: Optional[str] = None
    sort_order: Optional[int] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not is_http_url(value):
            raise ValueError("url must start with http:// or https://")
        return value


class MaterialResponse(BaseModel):
    id: uuid.UUID
    lesson_id: uuid.UUID
    kind: str
    title: str
    url: Optional[str]
    embed_url: Optional[str] = None
    body: Optional[str]
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ---------- tasks ----------
class TaskCreate(BaseModel):
    kind: TaskKind = "practice"
    title: str = Field(min_length=1, max_length=200)
    statement: Optional[str] = None
    starter_code: Optional[str] = None
    tests: Optional[Dict[str, Any]] = None
    hint: Optional[str] = None


class TaskUpdate(BaseModel):
    kind: Optional[TaskKind] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    statement: Optional[str] = None
    starter_code: Optional[str] = None
    tests: Optional[Dict[str, Any]] = None
    hint: Optional[str] = None
    sort_order: Optional[int] = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lesson_id: uuid.UUID
    kind: str
    title: str
    statement: Optional[str]
    starter_code: Optional[str]
    tests: Optional[Dict[str, Any]]
    hint: Optional[str]
    sort_order: int


class LessonDetail(LessonResponse):
    course_id: uuid.UUID
    course_title: str
    module_title: str
    materials: List[MaterialResponse] = Field(default_factory=list)
    tasks: List[TaskResponse] = Field(default_factory=list)


# ---------- enrollments / progress / notes ----------
class EnrollmentCreate(BaseModel):
    course_id: uuid.UUID


class EnrollmentUpdate(BaseModel):
    status: Optional[EnrollmentStatus] = None


class StudentCourseSet(BaseModel):
    """Заменить курс ученика одним действием: course_id = null снимает курс."""

    course_id: Optional[uuid.UUID] = None


class NextLesson(BaseModel):
    id: uuid.UUID
    title: str
    scheduled_at: Optional[datetime] = None  # дата и время этого урока по расписанию ученика


class ScheduleSlotBrief(BaseModel):
    """Действующий слот расписания ученика (задаётся в карточке ученика, здесь только показывается)."""

    day_of_week: int  # 0 = понедельник ... 6 = воскресенье
    start_time: time
    duration_minutes: int
    valid_from: date


class EnrollmentResponse(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    course_id: uuid.UUID
    course_title: str
    course_type: str
    status: str
    started_at: datetime
    lessons_total: int = 0
    lessons_done: int = 0
    next_lesson: Optional[NextLesson] = None
    schedule: List[ScheduleSlotBrief] = Field(default_factory=list)
    first_lesson_at: Optional[datetime] = None
    last_lesson_at: Optional[datetime] = None  # заполнено, только когда расписание покрывает все уроки курса
    lessons_scheduled: int = 0  # сколько уроков курса уже получили дату
    lessons_held: int = 0  # сколько уроков уже прошло по календарю


class ProgressUpdate(BaseModel):
    status: ProgressStatus


class ProgressResponse(BaseModel):
    student_id: uuid.UUID
    lesson_id: uuid.UUID
    status: str
    updated_at: datetime


class StudentCourseProgress(BaseModel):
    student_id: uuid.UUID
    course_id: uuid.UUID
    course_title: str
    lessons_total: int
    lessons_done: int
    modules: List[ModuleTree] = Field(default_factory=list)


class NoteCreate(BaseModel):
    content: str = Field(min_length=1)
    lesson_id: Optional[uuid.UUID] = None


class NoteUpdate(BaseModel):
    content: str = Field(min_length=1)


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    lesson_id: Optional[uuid.UUID]
    content: str
    created_at: datetime
    updated_at: datetime
