import pytest
from pydantic import ValidationError

from schemas.lms import CourseCreate, LessonCreate, MaterialCreate, MaterialUpdate, TaskCreate


def test_course_defaults_and_validation():
    course = CourseCreate(title="Python Start")
    assert course.course_type == "code"
    with pytest.raises(ValidationError):
        CourseCreate(title="")
    with pytest.raises(ValidationError):
        CourseCreate(title="X", course_type="video")


def test_lesson_duration_must_be_positive():
    assert LessonCreate(title="Intro").duration_min is None
    assert LessonCreate(title="Intro", duration_min=60).duration_min == 60
    with pytest.raises(ValidationError):
        LessonCreate(title="Intro", duration_min=0)


def test_material_url_is_validated_and_trimmed():
    material = MaterialCreate(title="Slides", url="  https://docs.google.com/presentation/d/abc  ")
    assert material.url == "https://docs.google.com/presentation/d/abc"
    for bad in ("javascript:alert(1)", "data:text/html,x", "just text"):
        with pytest.raises(ValidationError):
            MaterialCreate(title="Bad", url=bad)
    with pytest.raises(ValidationError):
        MaterialUpdate(url="file:///etc/passwd")


def test_task_kind_and_tests_payload():
    task = TaskCreate(title="Sum", kind="code", tests={"mode": "stdin_stdout", "cases": [{"input": "1\n2\n", "expected": "3"}]})
    assert task.tests["cases"][0]["expected"] == "3"
    with pytest.raises(ValidationError):
        TaskCreate(title="Bad", kind="quiz")


def test_enrollment_payloads_and_status_validation():
    from uuid import uuid4

    from schemas.lms import EnrollmentCreate, EnrollmentUpdate, StudentCourseSet

    assert EnrollmentCreate(course_id=uuid4()).course_id is not None
    assert EnrollmentUpdate().status is None
    assert StudentCourseSet().course_id is None
    with pytest.raises(ValidationError):
        EnrollmentUpdate(status="stopped")


def test_enrollment_response_carries_schedule_view():
    from datetime import date, datetime, time
    from uuid import uuid4

    from schemas.lms import EnrollmentResponse, NextLesson, ScheduleSlotBrief

    now = datetime(2026, 11, 10, 17, 0)
    response = EnrollmentResponse(
        id=uuid4(), student_id=uuid4(), course_id=uuid4(), course_title="Python", course_type="code", status="active",
        started_at=now, lessons_total=42, lessons_scheduled=42, first_lesson_at=now,
        next_lesson=NextLesson(id=uuid4(), title="Intro", scheduled_at=now),
        schedule=[ScheduleSlotBrief(day_of_week=1, start_time=time(17, 0), duration_minutes=60, valid_from=date(2026, 11, 10))],
    )
    assert response.schedule[0].day_of_week == 1
    assert response.last_lesson_at is None
