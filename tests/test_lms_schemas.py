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
