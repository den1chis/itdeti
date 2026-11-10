"""Schemas of the student site (/student/...) and of the admin "student access" block (/students/{id}/access).

Student-facing schemas list their fields explicitly: e.g. StudentTask has no `tests`,
so the hidden autograder data can never leak into a student response."""
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

StudentProgressStatus = Literal["in_progress", "done"]


# ---------------------------------------------------------------- student site
class StudentLoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class StudentLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    full_name: str


class StudentMe(BaseModel):
    full_name: str


class StudentCourseItem(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    course_type: str
    enrollment_status: str
    lessons_total: int
    lessons_done: int


class StudentLessonBrief(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    duration_min: Optional[int]
    status: str


class StudentModule(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    lessons: List[StudentLessonBrief] = Field(default_factory=list)


class StudentCourseDetail(StudentCourseItem):
    modules: List[StudentModule] = Field(default_factory=list)


class StudentMaterial(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    url: Optional[str]
    embed_url: Optional[str]
    body: Optional[str]


class StudentTask(BaseModel):
    # NB: no `tests` field on purpose.
    id: uuid.UUID
    kind: str
    title: str
    statement: Optional[str]
    starter_code: Optional[str]
    hint: Optional[str]


class StudentLessonDetail(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    duration_min: Optional[int]
    status: str
    course_id: uuid.UUID
    course_title: str
    module_id: uuid.UUID
    module_title: str
    prev_lesson_id: Optional[uuid.UUID]
    next_lesson_id: Optional[uuid.UUID]
    materials: List[StudentMaterial] = Field(default_factory=list)
    tasks: List[StudentTask] = Field(default_factory=list)


class StudentProgressUpdate(BaseModel):
    status: StudentProgressStatus


class StudentProgressResponse(BaseModel):
    lesson_id: uuid.UUID
    status: str


# ---------------------------------------------------------------- admin: student access
class AccessCreate(BaseModel):
    login: Optional[str] = Field(default=None, max_length=32)


class AccessUpdate(BaseModel):
    login: Optional[str] = Field(default=None, max_length=32)
    is_active: Optional[bool] = None


class AccessStatus(BaseModel):
    student_id: uuid.UUID
    exists: bool
    login: Optional[str] = None
    is_active: Optional[bool] = None
    failed_attempts: Optional[int] = None
    locked_until: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AccessCredentials(AccessStatus):
    # The plain password, right after create / reset. Night 2: it can also be viewed later
    # (GET /students/{id}/access/password), it is stored encrypted next to the bcrypt hash.
    password: str


class AccessPassword(BaseModel):
    student_id: uuid.UUID
    login: str
    password: str


class LoginSuggestion(BaseModel):
    login: str
