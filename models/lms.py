"""LMS catalog models. All tables are new and prefixed with lms_; no relationship() is used on purpose,
so nothing here can affect mapper configuration of the existing models."""
import uuid

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, SmallInteger, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from core.database import Base


class LmsCourse(Base):
    __tablename__ = "lms_courses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    course_type = Column(String, nullable=False, default="code")
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsModule(Base):
    __tablename__ = "lms_modules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id = Column(UUID(as_uuid=True), ForeignKey("lms_courses.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsLesson(Base):
    __tablename__ = "lms_lessons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module_id = Column(UUID(as_uuid=True), ForeignKey("lms_modules.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    duration_min = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsMaterial(Base):
    __tablename__ = "lms_materials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lms_lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False, default="link")
    title = Column(String, nullable=False)
    url = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsTask(Base):
    __tablename__ = "lms_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lms_lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False, default="practice")
    title = Column(String, nullable=False)
    statement = Column(Text, nullable=True)
    starter_code = Column(Text, nullable=True)
    tests = Column(JSONB, nullable=True)
    hint = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsEnrollment(Base):
    __tablename__ = "lms_enrollments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("lms_courses.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False, default="active")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    # Расписание назначения (используется на этапе автоматической расстановки уроков в календаре).
    start_date = Column(Date, nullable=True)
    weekday = Column(SmallInteger, nullable=True)  # 0 = понедельник ... 6 = воскресенье
    lesson_time = Column(Time, nullable=True)


class LmsLessonProgress(Base):
    __tablename__ = "lms_lesson_progress"

    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lms_lessons.id", ondelete="CASCADE"), primary_key=True)
    status = Column(String, nullable=False, default="not_started")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsStudentNote(Base):
    __tablename__ = "lms_student_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lms_lessons.id", ondelete="SET NULL"), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
