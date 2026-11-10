"""Integration tests of the student site API against the DEV database (.env).

Every run creates its own uniquely named rows (students, courses, accounts, users) and deletes them at the end.
Refuses to run against a non-dev database (scripts/devguard.py)."""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from core.config import settings
from scripts.devguard import is_dev_database

if not is_dev_database(settings.DATABASE_URL):  # pragma: no cover
    pytest.skip("integration tests run only against the dev database", allow_module_level=True)

import main  # noqa: E402
from core.database import AsyncSessionLocal  # noqa: E402
from core.security import create_access_token, hash_password  # noqa: E402
from core.student_auth import create_student_token  # noqa: E402
from models.lms import LmsCourse, LmsEnrollment, LmsLesson, LmsLessonProgress, LmsMaterial, LmsModule, LmsTask  # noqa: E402
from models.student import Student  # noqa: E402
from models.student_account import StudentAccount  # noqa: E402
from models.user import User  # noqa: E402
from routers.student_site import login_limiter  # noqa: E402

RUN = uuid.uuid4().hex[:6]
PASSWORD = "246810"
SECRET_TESTS = {"cases": [{"input": "", "expected_output": f"SECRET-{RUN}"}]}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def run(client, fn, *args):
    return client.portal.call(fn, *args)


async def _create_world():
    w = {}
    async with AsyncSessionLocal() as db:
        def add(obj):
            db.add(obj)
            return obj

        w["admin"] = add(User(email=f"t-admin-{RUN}@example.com", full_name="t admin", role="admin",
                              is_active=True, password_hash=hash_password("x-" + RUN)))
        w["teacher"] = add(User(email=f"t-teacher-{RUN}@example.com", full_name="t teacher", role="teacher",
                                is_active=True, password_hash=hash_password("x-" + RUN)))
        for key in ("A", "B", "C", "D"):
            w[key] = add(Student(full_name=f"Тест{key} {RUN}", lesson_price=1, balance=0, is_active=True, notes="pytest"))
        await db.flush()

        def course(name):
            c = add(LmsCourse(title=f"pytest {name} {RUN}", course_type="code"))
            return c

        for name in ("X", "Y", "Z"):
            w["course_" + name] = course(name)
        await db.flush()
        for name in ("X", "Y", "Z"):
            m = add(LmsModule(course_id=w["course_" + name].id, title="m1", sort_order=0))
            await db.flush()
            lessons = [add(LmsLesson(module_id=m.id, title=f"l{i}", sort_order=i)) for i in range(2)]
            await db.flush()
            w["lessons_" + name] = lessons
            add(LmsMaterial(lesson_id=lessons[0].id, kind="link", title="slides", url="https://example.com/s"))
            add(LmsTask(lesson_id=lessons[0].id, kind="code", title="code task", statement="print", tests=SECRET_TESTS))
            add(LmsTask(lesson_id=lessons[0].id, kind="practice", title="practice task"))
        add(LmsEnrollment(student_id=w["A"].id, course_id=w["course_X"].id, status="active"))
        add(LmsEnrollment(student_id=w["B"].id, course_id=w["course_Y"].id, status="active"))
        add(LmsEnrollment(student_id=w["C"].id, course_id=w["course_X"].id, status="active"))
        for key in ("A", "B", "C"):
            add(StudentAccount(student_id=w[key].id, login=f"t{key.lower()}{RUN}", is_active=True,
                               password_hash=hash_password(PASSWORD)))
        await db.commit()
    return {k: v for k, v in w.items()}


async def _cleanup(w):
    async with AsyncSessionLocal() as db:
        student_ids = [w[k].id for k in ("A", "B", "C", "D")]
        course_ids = [w["course_" + n].id for n in ("X", "Y", "Z")]
        lesson_ids = [l.id for n in ("X", "Y", "Z") for l in w["lessons_" + n]]
        # The dev DB has no ON DELETE CASCADE on the LMS tables, so delete children first.
        await db.execute(delete(LmsLessonProgress).where(LmsLessonProgress.student_id.in_(student_ids)))
        await db.execute(delete(LmsEnrollment).where(LmsEnrollment.student_id.in_(student_ids)))
        await db.execute(delete(StudentAccount).where(StudentAccount.student_id.in_(student_ids)))
        await db.execute(delete(LmsTask).where(LmsTask.lesson_id.in_(lesson_ids)))
        await db.execute(delete(LmsMaterial).where(LmsMaterial.lesson_id.in_(lesson_ids)))
        await db.execute(delete(LmsLesson).where(LmsLesson.id.in_(lesson_ids)))
        await db.execute(delete(LmsModule).where(LmsModule.course_id.in_(course_ids)))
        await db.execute(delete(LmsCourse).where(LmsCourse.id.in_(course_ids)))
        await db.execute(delete(Student).where(Student.id.in_(student_ids)))
        await db.execute(delete(User).where(User.id.in_([w["admin"].id, w["teacher"].id])))
        await db.commit()


@pytest.fixture(scope="module")
def world(client):
    w = run(client, _create_world)
    yield w
    run(client, _cleanup, w)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    login_limiter.reset()
    yield
    login_limiter.reset()


def login(client, login_, password=PASSWORD):
    return client.post("/student/auth/login", json={"login": login_, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def token_of(client, w, key):
    r = login(client, f"t{key.lower()}{RUN}")
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _account(student_id):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(StudentAccount).where(StudentAccount.student_id == student_id))


# ---------------------------------------------------------------- login
def test_login_success(client, world):
    r = login(client, f"  TA{RUN}  ")  # login is case-insensitive and trimmed
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["full_name"] == world["A"].full_name
    me = client.get("/student/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200 and me.json() == {"full_name": world["A"].full_name}
    assert run(client, _account, world["A"].id).last_login_at is not None


def test_login_wrong_password_and_unknown_login(client, world):
    r = login(client, f"ta{RUN}", "000000")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"
    r = login(client, f"nobody{RUN}", "000000")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"  # same answer: no login enumeration


def test_lockout_after_five_failures(client, world):
    name = f"tc{RUN}"
    for _ in range(4):
        assert login(client, name, "999999").status_code == 401
    r = login(client, name, "999999")
    assert r.status_code == 423
    assert r.json()["detail"]["code"] == "locked"
    assert r.json()["detail"]["retry_after_minutes"] == 15
    # Even the right password does not get in while locked.
    r = login(client, name)
    assert r.status_code == 423
    assert 1 <= r.json()["detail"]["retry_after_minutes"] <= 15
    account = run(client, _account, world["C"].id)
    assert account.locked_until > datetime.now(timezone.utc) + timedelta(minutes=14)


def test_login_rate_limited_per_ip(client, world):
    codes = [login(client, f"nobody{RUN}", "000000").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


# ---------------------------------------------------------------- isolation between students
def test_student_sees_only_own_course(client, world):
    token = token_of(client, world, "A")
    courses = client.get("/student/courses", headers=bearer(token)).json()
    assert [c["id"] for c in courses] == [str(world["course_X"].id)]
    assert client.get(f"/student/courses/{world['course_X'].id}", headers=bearer(token)).status_code == 200
    # B's course and a course nobody is enrolled in.
    for other in ("course_Y", "course_Z"):
        r = client.get(f"/student/courses/{world[other].id}", headers=bearer(token))
        assert r.status_code == 404, other
    for other in ("lessons_Y", "lessons_Z"):
        lesson_id = world[other][0].id
        assert client.get(f"/student/lessons/{lesson_id}", headers=bearer(token)).status_code == 404
        r = client.post(f"/student/lessons/{lesson_id}/progress", headers=bearer(token), json={"status": "done"})
        assert r.status_code == 404


def test_student_id_in_query_is_ignored(client, world):
    token = token_of(client, world, "A")
    r = client.get("/student/courses", params={"student_id": str(world["B"].id)}, headers=bearer(token))
    assert [c["id"] for c in r.json()] == [str(world["course_X"].id)]


def test_progress_and_course_tree(client, world):
    token = token_of(client, world, "A")
    l0, l1 = world["lessons_X"]
    assert client.post(f"/student/lessons/{l0.id}/progress", headers=bearer(token), json={"status": "done"}).json()["status"] == "done"
    # Re-opening a finished lesson does not undo "done".
    assert client.post(f"/student/lessons/{l0.id}/progress", headers=bearer(token), json={"status": "in_progress"}).json()["status"] == "done"
    assert client.post(f"/student/lessons/{l1.id}/progress", headers=bearer(token), json={"status": "bogus"}).status_code == 422
    tree = client.get(f"/student/courses/{world['course_X'].id}", headers=bearer(token)).json()
    statuses = [l["status"] for m in tree["modules"] for l in m["lessons"]]
    assert statuses == ["done", "not_started"] and tree["lessons_done"] == 1 and tree["lessons_total"] == 2
    # Student C is on the same course: A's progress must not show up as C's progress.
    assert run(client, _progress, world["C"].id) == []


async def _progress(student_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(LmsLessonProgress).where(LmsLessonProgress.student_id == student_id))).scalars().all()


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def test_task_tests_are_not_sent_to_student(client, world):
    token = token_of(client, world, "A")
    r = client.get(f"/student/lessons/{world['lessons_X'][0].id}", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert {t["kind"] for t in body["tasks"]} == {"code", "practice"}
    assert "tests" not in set(_all_keys(body))
    assert f"SECRET-{RUN}" not in r.text
    assert body["next_lesson_id"] == str(world["lessons_X"][1].id) and body["prev_lesson_id"] is None


# ---------------------------------------------------------------- role isolation
PUBLIC = {("POST", "/auth/login"), ("POST", "/auth/refresh"), ("POST", "/auth/logout"), ("GET", "/health")}


def _crm_routes():
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith("/student/"):
            continue
        for method in route.methods:
            if (method, route.path) not in PUBLIC:
                yield method, route.path


def test_student_token_is_rejected_by_every_crm_route(client, world):
    token = token_of(client, world, "A")
    checked = 0
    for method, path in _crm_routes():
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
        r = client.request(method, url, headers=bearer(token), json={})
        assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code}"
        checked += 1
    assert checked > 50
    # The refresh endpoint does not turn a student token into a CRM one.
    assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401


def test_crm_token_is_rejected_by_student_routes(client, world):
    crm = create_access_token(str(world["admin"].id), "admin")
    assert client.get("/student/me", headers=bearer(crm)).status_code == 401
    assert client.get("/student/courses").status_code == 401
    # A forged "student" token for a student without an account is useless too.
    assert client.get("/student/me", headers=bearer(create_student_token(world["D"].id))).status_code == 401


# ---------------------------------------------------------------- admin: access management
def test_admin_creates_resets_and_disables_access(client, world):
    admin = bearer(create_access_token(str(world["admin"].id), "admin"))
    teacher = bearer(create_access_token(str(world["teacher"].id), "teacher"))
    sid = world["D"].id

    # Night 2 (intentional change): a teacher may see access of OWN students; a foreign student -> 404, not 403.
    assert client.get(f"/students/{sid}/access", headers=teacher).status_code == 404
    assert client.get(f"/students/{sid}/access", headers=admin).json()["exists"] is False

    suggestion = client.get(f"/students/{sid}/access/login-suggestion", headers=admin).json()["login"]
    assert re.fullmatch(r"test[a-d]\d{2,3}", suggestion)  # "ТестD ..." -> "testd" + 2-3 digits

    r = client.post(f"/students/{sid}/access", headers=admin, json={"login": f"tD{RUN}x"})
    assert r.status_code == 201, r.text
    creds = r.json()
    assert creds["login"] == f"td{RUN}x" and re.fullmatch(r"\d{6}", creds["password"])
    assert r.headers["cache-control"] == "no-store"
    assert run(client, _account, sid).password_hash != creds["password"]  # only the hash is stored
    assert "password" not in client.get(f"/students/{sid}/access", headers=admin).json()
    assert client.post(f"/students/{sid}/access", headers=admin, json={}).status_code == 409
    # Login of another student is taken.
    assert client.patch(f"/students/{sid}/access", headers=admin, json={"login": f"ta{RUN}"}).status_code == 409
    assert client.patch(f"/students/{sid}/access", headers=admin, json={"login": "Плохой"}).status_code == 400

    first = login(client, creds["login"], creds["password"])
    assert first.status_code == 200
    token = first.json()["access_token"]

    reset = client.post(f"/students/{sid}/access/reset-password", headers=admin).json()
    assert re.fullmatch(r"\d{6}", reset["password"])
    assert login(client, creds["login"], reset["password"]).status_code == 200
    if reset["password"] != creds["password"]:  # equal only by a one-in-a-million chance
        assert login(client, creds["login"], creds["password"]).status_code == 401

    # Switching access off works immediately, also for an already issued token.
    assert client.patch(f"/students/{sid}/access", headers=admin, json={"is_active": False}).json()["is_active"] is False
    assert client.get("/student/me", headers=bearer(token)).status_code == 401
    r = login(client, creds["login"], reset["password"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "disabled"
    assert client.patch(f"/students/{sid}/access", headers=admin, json={"is_active": True}).json()["is_active"] is True
    assert login(client, creds["login"], reset["password"]).status_code == 200
