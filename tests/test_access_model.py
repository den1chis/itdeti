"""Night 2: access model (roles, ownership, teacher routes, staff management, student passwords).

Integration tests against the DEV database (.env), like tests/test_student_site.py: every run creates its own
uniquely named rows and deletes them at the end. Refuses to run against a non-dev database."""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from core.config import settings
from scripts.devguard import is_dev_database

if not is_dev_database(settings.DATABASE_URL):  # pragma: no cover
    pytest.skip("integration tests run only against the dev database", allow_module_level=True)

import main  # noqa: E402
from core import access  # noqa: E402
from core.database import AsyncSessionLocal  # noqa: E402
from core.lesson_service import complete_overdue_lessons  # noqa: E402
from core.security import create_access_token, hash_password, verify_password  # noqa: E402
from core.student_auth import create_student_token  # noqa: E402
from core.student_secrets import decrypt_password  # noqa: E402
from models.lesson import Lesson  # noqa: E402
from models.lms import LmsCourse, LmsEnrollment, LmsLesson, LmsModule, LmsStudentNote  # noqa: E402
from models.parent import Parent, StudentParent  # noqa: E402
from models.refresh_token import RefreshToken  # noqa: E402
from models.schedule import Event, StudentScheduleSlot  # noqa: E402
from models.student import Student  # noqa: E402
from models.student_account import StudentAccount  # noqa: E402
from models.user import User  # noqa: E402

RUN = uuid.uuid4().hex[:6]
NOW = datetime.now(timezone.utc)
B_PARENT_PHONE = f"+7999{int(RUN, 16) % 10_000_000:07d}"
MONEY_KEYS = {"balance", "lesson_price", "price", "amount", "payments", "balance_deducted", "current_month_due",
              "monthly_debt", "payments_this_month", "remaining_lessons", "income", "expenses", "total"}
PRIVATE_KEYS = {"birth_date", "teacher_notes", "notes"}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def run(client, fn, *args):
    return client.portal.call(fn, *args)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


# ---------------------------------------------------------------- test world
USERS = {
    "admin": ["admin"], "admin2": ["admin"], "A": ["teacher"], "B": ["teacher"], "C": ["teacher"],
    "M": ["methodist"], "TM": ["teacher", "methodist"],
}


async def _create_world():
    w = {}
    async with AsyncSessionLocal() as db:
        def add(obj):
            db.add(obj)
            return obj

        for key, roles in USERS.items():
            w[key] = add(User(email=f"t2-{key.lower()}-{RUN}@example.com", full_name=f"t2 {key} {RUN}", role=roles[0],
                              roles=list(roles), is_active=True, password_hash=hash_password("pw-" + RUN)))
        await db.flush()
        owners = {"SA1": "A", "SA2": "A", "SB1": "B", "SC": "C", "SN": None}
        for key, owner in owners.items():
            w[key] = add(Student(full_name=f"Т2{key} {RUN}", lesson_price=Decimal("1000"), balance=Decimal("5000"),
                                 is_active=True, notes=f"private-note-{RUN}", course=f"Курс {key}",
                                 teacher_id=w[owner].id if owner else None))
        w["course"] = add(LmsCourse(title=f"pytest access {RUN}", course_type="code"))
        await db.flush()
        module = add(LmsModule(course_id=w["course"].id, title="m", sort_order=0))
        await db.flush()
        w["module"] = module
        w["lms_lesson"] = add(LmsLesson(module_id=module.id, title="l0", sort_order=0))
        for key in ("SA1", "SB1"):
            add(LmsEnrollment(student_id=w[key].id, course_id=w["course"].id, status="active"))
        w["parent_b"] = add(Parent(full_name=f"Родитель Б {RUN}", phone=B_PARENT_PHONE, whatsapp_phone=B_PARENT_PHONE))
        w["parent_a"] = add(Parent(full_name=f"Родитель А {RUN}", phone="+70000000001"))
        await db.flush()
        add(StudentParent(student_id=w["SB1"].id, parent_id=w["parent_b"].id))
        add(StudentParent(student_id=w["SA1"].id, parent_id=w["parent_a"].id))
        # SA1, SB1 get viewable passwords through the API in the tests; SA2 is a night-1 account (no password_enc).
        for key in ("SA1", "SA2", "SB1"):
            add(StudentAccount(student_id=w[key].id, login=f"t2{key.lower()}{RUN}", is_active=True,
                               password_hash=hash_password("123456")))
        add(StudentScheduleSlot(student_id=w["SA1"].id, day_of_week=0, start_time=datetime(2000, 1, 1, 16, 0).time(),
                                duration_minutes=60, is_active=True, valid_from=NOW.date() + timedelta(days=400)))
        w["note_b"] = add(LmsStudentNote(student_id=w["SB1"].id, content=f"note about B {RUN}"))
        w["note_a"] = add(LmsStudentNote(student_id=w["SA1"].id, content=f"note about A {RUN}"))

        def lesson(student_key, start, teacher_key=None, status="scheduled", topic=None):
            event = add(Event(title=f"t2 {student_key} {RUN}", event_type="personal", start_time=start,
                              end_time=start + timedelta(hours=1)))
            return event, Lesson(student_id=w[student_key].id, lesson_kind="lesson", lesson_type="regular",
                                 status=status, topic=topic, price=Decimal("1000"), teacher_notes=f"admin-only {RUN}",
                                 teacher_id=w[teacher_key].id if teacher_key else None)

        soon = NOW + timedelta(days=2)
        plan = {
            "lesson_a": ("SA1", soon, None),
            "lesson_b": ("SB1", soon + timedelta(hours=2), None),
            "lesson_b_sub_by_a": ("SB1", soon + timedelta(hours=4), "A"),
            "lesson_sn_sub_by_c": ("SN", soon + timedelta(hours=6), "C"),
        }
        pending = {}
        for key, (student_key, start, teacher_key) in plan.items():
            pending[key] = lesson(student_key, start, teacher_key, topic=f"topic {key}")
        await db.flush()
        for key, (event, les) in pending.items():
            les.event_id = event.id
            w[key] = add(les)
        await db.commit()
    return w


async def _cleanup(w):
    async with AsyncSessionLocal() as db:
        student_ids = [w[k].id for k in ("SA1", "SA2", "SB1", "SC", "SN")]
        user_ids = [w[k].id for k in USERS] + list(w.get("extra_users", []))
        event_ids = (await db.execute(select(Lesson.event_id).where(Lesson.student_id.in_(student_ids)))).scalars().all()
        await db.execute(delete(Lesson).where(Lesson.student_id.in_(student_ids)))
        await db.execute(delete(Event).where(Event.id.in_([e for e in event_ids if e])))
        await db.execute(delete(StudentScheduleSlot).where(StudentScheduleSlot.student_id.in_(student_ids)))
        await db.execute(delete(LmsStudentNote).where(LmsStudentNote.student_id.in_(student_ids)))
        await db.execute(delete(LmsEnrollment).where(LmsEnrollment.student_id.in_(student_ids)))
        await db.execute(delete(StudentAccount).where(StudentAccount.student_id.in_(student_ids)))
        await db.execute(delete(StudentParent).where(StudentParent.student_id.in_(student_ids)))
        await db.execute(delete(Parent).where(Parent.id.in_([w["parent_a"].id, w["parent_b"].id])))
        await db.execute(delete(LmsLesson).where(LmsLesson.module_id == w["module"].id))
        await db.execute(delete(LmsModule).where(LmsModule.course_id == w["course"].id))
        await db.execute(delete(LmsCourse).where(LmsCourse.title.like(f"%{RUN}%")))
        await db.execute(delete(Student).where(Student.id.in_(student_ids)))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


@pytest.fixture(scope="module")
def world(client):
    w = run(client, _create_world)
    w["extra_users"] = []
    yield w
    run(client, _cleanup, w)


def token(w, key):
    return bearer(create_access_token(str(w[key].id), USERS[key][0]))


async def _get(model, row_id):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(model).where(model.id == row_id))


async def _account(student_id):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(StudentAccount).where(StudentAccount.student_id == student_id))


# ---------------------------------------------------------------- 1. every route is marked
def test_every_route_has_an_access_class():
    assert access.unmarked_routes(main.app) == []


def test_unmarked_route_is_detected():
    app = FastAPI()
    router = APIRouter()

    @router.get("/naked")
    async def naked():
        return {}

    @router.get("/marked", dependencies=[Depends(access.require_admin)])
    async def marked():
        return {}

    app.include_router(router)
    assert ("GET", "/naked") in access.unmarked_routes(app)
    assert ("GET", "/marked") not in access.unmarked_routes(app)


def test_roles_of_falls_back_to_single_role():
    class U:
        role, roles = "teacher", []

    assert access.roles_of(U()) == {"teacher"}
    U.roles = ["teacher", "methodist"]
    assert access.roles_of(U()) == {"teacher", "methodist"}


# ---------------------------------------------------------------- 2. route matrix
ROLE_SETS = {"anonymous": None, "student": "student", "A": {"teacher"}, "M": {"methodist"},
             "TM": {"teacher", "methodist"}, "admin": {"admin"}}
# Calling these as admin would really change dev data (no body, no path id): only the class is checked.
ADMIN_SKIP_CALL = {("POST", "/finance/recurring-expenses/sync")}


def _expected_allowed(route, role_set) -> bool:
    labels = access.access_markers(route)
    if role_set is None:
        return False
    if role_set == "student":
        return labels == ["student"]
    if "student" in labels:
        return False
    if "admin" in labels:
        return "admin" in role_set
    allowed = set()
    for label in labels:
        allowed |= {r.strip() for r in label.split(":", 1)[1].split(",")}
    return bool(allowed & role_set)


def test_route_matrix(client, world):
    headers = {
        "anonymous": {},
        "student": bearer(create_student_token(world["SA1"].id)),
        "A": token(world, "A"), "M": token(world, "M"), "TM": token(world, "TM"), "admin": token(world, "admin"),
    }
    checked, failures = 0, []
    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD"}):
            if (method, route.path) in access.PUBLIC_ROUTES:
                continue
            # A path parameter that is not a UUID: access dependencies run first (401/403), then validation
            # fails with 422 — so an allowed role is proven without running the handler (fast, changes nothing).
            url = re.sub(r"\{[^}]+\}", "not-a-uuid", route.path)
            for role, role_set in ROLE_SETS.items():
                allowed = _expected_allowed(route, role_set)
                if allowed and method != "GET" and role == "admin" and (method, route.path) in ADMIN_SKIP_CALL:
                    continue
                if allowed and method != "GET" and role == "student":
                    continue  # student POST routes are covered by tests/test_student_site.py
                r = client.request(method, url, headers=headers[role], json={})
                if allowed:
                    ok = r.status_code not in (401, 403)
                elif "{" in route.path:
                    ok = r.status_code in (401, 403)  # refused before validation, i.e. by the access class
                else:
                    ok = r.status_code in (401, 403, 404)
                if not ok:
                    failures.append(f"{role:9} {method:6} {route.path} -> {r.status_code} (allowed={allowed})")
                checked += 1
    assert not failures, "\n".join(failures)
    assert checked > 500


# ---------------------------------------------------------------- 3. isolation teacher A <-> teacher B
def test_teacher_sees_only_own_students(client, world):
    a = token(world, "A")
    students = client.get("/teacher/students", headers=a).json()
    assert {s["id"] for s in students} == {str(world["SA1"].id), str(world["SA2"].id)}
    sa1 = next(s for s in students if s["id"] == str(world["SA1"].id))
    assert sa1["parents"] == [{"full_name": f"Родитель А {RUN}", "phone": "+70000000001", "whatsapp_phone": None}]
    assert sa1["has_access"] is True and sa1["course"] == "Курс SA1"
    card = client.get(f"/teacher/students/{world['SA1'].id}", headers=a)
    assert card.status_code == 200
    assert [s["day_of_week"] for s in card.json()["schedule"]] == [0]
    me = client.get("/teacher/me", headers=a).json()
    assert me["students_count"] == 2 and me["roles"] == ["teacher"]


def test_teacher_a_gets_nothing_of_teacher_b(client, world):
    a = token(world, "A")
    sb = world["SB1"].id
    student_routes = [
        ("GET", f"/teacher/students/{sb}"),
        ("GET", f"/lms/students/{sb}/enrollments"),
        ("GET", f"/lms/students/{sb}/courses/{world['course'].id}/progress"),
        ("GET", f"/lms/students/{sb}/notes"),
        ("POST", f"/lms/students/{sb}/notes"),
        ("GET", f"/students/{sb}/access"),
        ("GET", f"/students/{sb}/access/password"),
        ("POST", f"/students/{sb}/access/reset-password"),
        ("POST", f"/students/{sb}/access/unlock"),
        ("PATCH", f"/lms/notes/{world['note_b'].id}"),
        ("DELETE", f"/lms/notes/{world['note_b'].id}"),
    ]
    for method, url in student_routes:
        r = client.request(method, url, headers=a, json={"content": "x"})
        assert r.status_code == 404, f"{method} {url} -> {r.status_code}"
    # Every teacher-visible list: nothing of B's student, no B parent phone, no B note.
    for url in ("/teacher/students", "/teacher/students?active_only=false", "/teacher/lessons",
                f"/teacher/lessons?from={NOW.date()}&to={(NOW + timedelta(days=10)).date()}", "/teacher/me"):
        r = client.get(url, headers=a)
        assert r.status_code == 200, url
        assert B_PARENT_PHONE not in r.text and f"note about B {RUN}" not in r.text
    lessons = client.get("/teacher/lessons", params={"from": str(NOW.date()), "to": str((NOW + timedelta(days=10)).date())}, headers=a).json()
    ids = {l["id"] for l in lessons}
    assert str(world["lesson_a"].id) in ids
    assert str(world["lesson_b"].id) not in ids  # B's normal lesson
    # The B note is untouched.
    assert run(client, _get, LmsStudentNote, world["note_b"].id).content == f"note about B {RUN}"


def test_teacher_writes_notes_for_own_student(client, world):
    a = token(world, "A")
    sa = world["SA1"].id
    r = client.post(f"/lms/students/{sa}/notes", headers=a, json={"content": f"own note {RUN}"})
    assert r.status_code == 201, r.text
    note_id = r.json()["id"]
    assert client.patch(f"/lms/notes/{note_id}", headers=a, json={"content": "edited"}).json()["content"] == "edited"
    assert any(n["id"] == note_id for n in client.get(f"/lms/students/{sa}/notes", headers=a).json())
    assert client.delete(f"/lms/notes/{note_id}", headers=a).status_code == 204
    # Writes on the student's course / progress stay admin only.
    assert client.put(f"/lms/students/{sa}/course", headers=a, json={"course_id": None}).status_code == 403
    assert client.put(f"/lms/students/{sa}/lessons/{world['lms_lesson'].id}/progress", headers=a,
                      json={"status": "done"}).status_code == 403


# ---------------------------------------------------------------- 4. no money for teachers
def _teacher_get_urls(world):
    values = {"student_id": world["SA1"].id, "course_id": world["course"].id, "lesson_id": world["lms_lesson"].id,
              "note_id": world["note_a"].id}
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if not _expected_allowed(route, {"teacher"}):
            continue
        params = re.findall(r"\{([^}]+)\}", route.path)
        url = route.path
        for p in params:
            url = url.replace("{" + p + "}", str(values[p]))
        yield route.path, url


def test_no_money_in_any_teacher_answer(client, world):
    a = token(world, "A")
    # Make the password viewable first (reset by the teacher himself).
    assert client.post(f"/students/{world['SA1'].id}/access/reset-password", headers=a).status_code == 200
    answers = {}
    for path, url in _teacher_get_urls(world):
        r = client.get(url, headers=a)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
        answers[path] = r.json()
    for url in ("/teacher/students?active_only=false",
                f"/teacher/lessons?from={NOW.date()}&to={(NOW + timedelta(days=10)).date()}"):
        answers[url] = client.get(url, headers=a).json()
    answers["POST notes"] = client.post(f"/lms/students/{world['SA1'].id}/notes", headers=a, json={"content": "n"}).json()
    answers["POST unlock"] = client.post(f"/students/{world['SA1'].id}/access/unlock", headers=a).json()
    answers["POST reset"] = client.post(f"/students/{world['SA1'].id}/access/reset-password", headers=a).json()
    assert len(answers) >= 14
    for path, body in answers.items():
        keys = set(_all_keys(body))
        assert not keys & MONEY_KEYS, f"{path}: money keys {keys & MONEY_KEYS}"
        if path.startswith("/teacher"):
            assert not keys & PRIVATE_KEYS, f"{path}: private keys {keys & PRIVATE_KEYS}"
            text = str(body)
            assert f"private-note-{RUN}" not in text and f"admin-only {RUN}" not in text


def test_admin_only_sections_refuse_teacher_and_methodist(client, world):
    for key in ("A", "M", "TM"):
        h = token(world, key)
        for url in ("/finance/summary", "/finance/payments", "/notifications", "/tasks/feed", "/events",
                    "/schedule/week", "/recurring-events", "/staff", "/students", "/parents", "/lessons",
                    f"/students/{world['SA1'].id}", f"/students/{world['SA1'].id}/balance"):
            assert client.get(url, headers=h).status_code == 403, f"{key} {url}"


# ---------------------------------------------------------------- 5. methodist, teacher+methodist
def test_methodist_edits_catalog_but_sees_no_students(client, world):
    m = token(world, "M")
    assert client.get("/lms/courses", headers=m).status_code == 200
    r = client.post("/lms/courses", headers=m, json={"title": f"pytest methodist {RUN}", "course_type": "no_code"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.patch(f"/lms/courses/{cid}", headers=m, json={"description": "d"}).status_code == 200
    assert client.delete(f"/lms/courses/{cid}", headers=m).status_code == 204
    for url in ("/teacher/students", "/teacher/me", f"/lms/students/{world['SA1'].id}/enrollments",
                f"/lms/students/{world['SA1'].id}/notes", f"/students/{world['SA1'].id}/access",
                f"/students/{world['SA1'].id}/access/password"):
        assert client.get(url, headers=m).status_code == 403, url
    # A plain teacher cannot write the catalog.
    assert client.post("/lms/courses", headers=token(world, "A"), json={"title": "x", "course_type": "code"}).status_code == 403


def test_teacher_methodist_has_both_roles_but_no_more(client, world):
    tm = token(world, "TM")
    me = client.get("/teacher/me", headers=tm).json()
    assert me["roles"] == ["teacher", "methodist"] and me["students_count"] == 0
    assert client.get("/teacher/students", headers=tm).json() == []
    assert client.get(f"/teacher/students/{world['SA1'].id}", headers=tm).status_code == 404
    assert client.get(f"/students/{world['SA1'].id}/access", headers=tm).status_code == 404
    assert client.post("/lms/courses", headers=tm, json={}).status_code == 422  # allowed (validation only)
    for url in ("/finance/summary", "/staff", "/students"):
        assert client.get(url, headers=tm).status_code == 403


# ---------------------------------------------------------------- 6. substitution
def test_substitute_sees_name_and_course_only(client, world):
    a, b = token(world, "A"), token(world, "B")
    window = {"from": str(NOW.date()), "to": str((NOW + timedelta(days=10)).date())}
    lessons = {l["id"]: l for l in client.get("/teacher/lessons", params=window, headers=a).json()}
    sub = lessons[str(world["lesson_b_sub_by_a"].id)]
    assert sub["is_substitute"] is True and sub["student_id"] is None and sub["topic"] is None
    assert sub["student_name"] == world["SB1"].full_name and sub["course"] == "Курс SB1"
    assert set(sub) == {"id", "is_substitute", "student_id", "student_name", "course", "start_time", "end_time",
                        "status", "topic", "lesson_type"}
    assert client.get(f"/teacher/students/{world['SB1'].id}", headers=a).status_code == 404
    # The permanent teacher still sees the student and his own lessons; the substituted one is A's.
    assert client.get(f"/teacher/students/{world['SB1'].id}", headers=b).status_code == 200
    b_lessons = {l["id"]: l for l in client.get("/teacher/lessons", params=window, headers=b).json()}
    assert str(world["lesson_b"].id) in b_lessons and b_lessons[str(world["lesson_b"].id)]["is_substitute"] is False
    assert str(world["lesson_b_sub_by_a"].id) not in b_lessons


def test_admin_assigns_teacher_and_substitute(client, world):
    admin = token(world, "admin")
    sn = world["SN"].id
    assert client.put(f"/students/{sn}/teacher", headers=admin, json={"teacher_id": str(world["M"].id)}).status_code == 400
    assert client.put(f"/students/{uuid.uuid4()}/teacher", headers=admin, json={"teacher_id": None}).status_code == 404
    r = client.put(f"/students/{sn}/teacher", headers=admin, json={"teacher_id": str(world["A"].id)})
    assert r.status_code == 200 and r.json()["teacher_id"] == str(world["A"].id)
    assert client.get(f"/teacher/students/{sn}", headers=token(world, "A")).status_code == 200
    assert client.put(f"/students/{sn}/teacher", headers=admin, json={"teacher_id": None}).json()["teacher_id"] is None
    assert client.get(f"/teacher/students/{sn}", headers=token(world, "A")).status_code == 404
    # Substitute: future lesson only, teacher role required.
    lesson_a = world["lesson_a"].id
    assert client.put(f"/lessons/{lesson_a}/teacher", headers=admin, json={"teacher_id": str(world["M"].id)}).status_code == 400
    r = client.put(f"/lessons/{lesson_a}/teacher", headers=admin, json={"teacher_id": str(world["TM"].id)})
    assert r.status_code == 200
    window = {"from": str(NOW.date()), "to": str((NOW + timedelta(days=10)).date())}
    tm_lessons = client.get("/teacher/lessons", params=window, headers=token(world, "TM")).json()
    assert [l["id"] for l in tm_lessons] == [str(lesson_a)] and tm_lessons[0]["is_substitute"] is True
    assert client.put(f"/lessons/{lesson_a}/teacher", headers=admin, json={"teacher_id": None}).json()["teacher_id"] is None
    # Teachers cannot assign anything.
    assert client.put(f"/students/{sn}/teacher", headers=token(world, "A"), json={"teacher_id": None}).status_code == 403
    assert client.put(f"/lessons/{lesson_a}/teacher", headers=token(world, "A"), json={"teacher_id": None}).status_code == 403


async def _past_lesson(student_id, teacher_id=None, minutes_ago=90):
    async with AsyncSessionLocal() as db:
        start = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        event = Event(title=f"t2 past {RUN}", event_type="personal", start_time=start, end_time=start + timedelta(minutes=60))
        db.add(event)
        await db.flush()
        lesson = Lesson(student_id=student_id, event_id=event.id, lesson_kind="lesson", lesson_type="regular",
                        status="scheduled", price=Decimal("1000"), teacher_id=teacher_id)
        db.add(lesson)
        await db.commit()
        return lesson.id


async def _complete():
    async with AsyncSessionLocal() as db:
        return await complete_overdue_lessons(db)


def test_substitute_cannot_be_set_on_a_past_lesson(client, world):
    past = run(client, _past_lesson, world["SA2"].id, None, 30)  # still running -> not completed yet
    r = client.put(f"/lessons/{past}/teacher", headers=token(world, "admin"), json={"teacher_id": str(world["B"].id)})
    assert r.status_code == 409


# ---------------------------------------------------------------- 7. actual teacher of a held lesson
def test_held_lesson_records_teacher_and_deduction_is_unchanged(client, world):
    sid = world["SA1"].id
    before = run(client, _get, Student, sid).balance
    own = run(client, _past_lesson, sid, None)
    substituted = run(client, _past_lesson, sid, world["B"].id)
    run(client, _complete)
    own_row, sub_row = run(client, _get, Lesson, own), run(client, _get, Lesson, substituted)
    assert own_row.status == "completed" and own_row.balance_deducted is True
    assert own_row.teacher_id == world["A"].id          # permanent teacher written in
    assert sub_row.teacher_id == world["B"].id          # substitute kept
    assert run(client, _get, Student, sid).balance == before - Decimal("2000")  # 2 x price, as before
    # Admin marks a lesson completed by hand: same deduction, teacher recorded.
    manual = run(client, _past_lesson, world["SA2"].id, None, 30)
    before2 = run(client, _get, Student, world["SA2"].id).balance
    r = client.patch(f"/lessons/{manual}", headers=token(world, "admin"), json={"status": "completed"})
    assert r.status_code == 200 and r.json()["balance_deducted"] is True and "teacher_id" not in r.json()
    assert run(client, _get, Lesson, manual).teacher_id == world["A"].id
    assert run(client, _get, Student, world["SA2"].id).balance == before2 - Decimal("1000")
    # Reassigning the student later does not rewrite the history.
    client.put(f"/students/{world['SA2'].id}/teacher", headers=token(world, "admin"), json={"teacher_id": str(world["B"].id)})
    assert run(client, _get, Lesson, manual).teacher_id == world["A"].id
    client.put(f"/students/{world['SA2'].id}/teacher", headers=token(world, "admin"), json={"teacher_id": str(world["A"].id)})


# ---------------------------------------------------------------- 8. staff management and dismissal
async def _refresh_count(user_id):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.user_id == user_id))


def test_staff_create_login_and_dismissal_rule(client, world):
    admin = token(world, "admin")
    email = f"t2-new-{RUN}@example.com"
    r = client.post("/staff", headers=admin, json={"email": email, "full_name": "Новый", "roles": ["teacher"]})
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    new = r.json()
    world["extra_users"].append(uuid.UUID(new["id"]))
    assert new["roles"] == ["teacher"] and len(new["password"]) >= 8
    assert client.post("/staff", headers=admin, json={"email": email.upper(), "full_name": "x", "roles": ["teacher"]}).status_code == 409
    assert client.post("/staff", headers=admin, json={"email": f"x{RUN}@example.com", "full_name": "x", "roles": []}).status_code == 422
    assert client.post("/staff", headers=admin, json={"email": f"x{RUN}@example.com", "full_name": "x", "roles": ["parent"]}).status_code == 422
    assert all("password" not in s for s in client.get("/staff", headers=admin).json())

    login = client.post("/auth/login", json={"email": email, "password": new["password"]})
    assert login.status_code == 200
    access_token, refresh_token = login.json()["access_token"], login.json()["refresh_token"]
    assert client.get("/teacher/me", headers=bearer(access_token)).status_code == 200

    # Give him a student and a future substitution.
    sc = world["SC"].id
    client.put(f"/students/{sc}/teacher", headers=admin, json={"teacher_id": new["id"]})
    client.put(f"/lessons/{world['lesson_sn_sub_by_c'].id}/teacher", headers=admin, json={"teacher_id": new["id"]})
    r = client.patch(f"/staff/{new['id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "has_students"
    assert {(b["kind"], b["id"]) for b in detail["blockers"]} == {("student", str(sc)), ("lesson", str(world["lesson_sn_sub_by_c"].id))}
    assert client.patch(f"/staff/{new['id']}", headers=admin, json={"roles": ["methodist"]}).status_code == 409
    # Reassign the student: the substitution still blocks.
    client.put(f"/students/{sc}/teacher", headers=admin, json={"teacher_id": str(world["C"].id)})
    r = client.patch(f"/staff/{new['id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 409 and [b["kind"] for b in r.json()["detail"]["blockers"]] == ["lesson"]
    client.put(f"/lessons/{world['lesson_sn_sub_by_c'].id}/teacher", headers=admin, json={"teacher_id": str(world["C"].id)})
    # Now it passes; sessions die at once.
    assert run(client, _refresh_count, uuid.UUID(new["id"])) == 1
    r = client.patch(f"/staff/{new['id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/teacher/me", headers=bearer(access_token)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401
    assert run(client, _refresh_count, uuid.UUID(new["id"])) == 0
    assert client.post("/auth/login", json={"email": email, "password": new["password"]}).status_code == 401


def test_role_change_takes_effect_immediately(client, world):
    admin = token(world, "admin")
    tm = token(world, "TM")  # token issued before the change
    assert client.patch(f"/staff/{world['TM'].id}", headers=admin, json={"roles": ["methodist"]}).status_code == 200
    assert client.get("/teacher/me", headers=tm).status_code == 403
    r = client.patch(f"/staff/{world['TM'].id}", headers=admin, json={"roles": ["teacher", "methodist"]})
    assert r.status_code == 200 and r.json()["roles"] == ["teacher", "methodist"]
    assert client.get("/teacher/me", headers=tm).status_code == 200


def test_staff_password_reset(client, world):
    admin = token(world, "admin")
    r = client.post(f"/staff/{world['B'].id}/reset-password", headers=admin, json={"password": f"Pw-{RUN}-long"})
    assert r.status_code == 200 and r.json()["password"] == f"Pw-{RUN}-long"
    assert client.post("/auth/login", json={"email": world["B"].email, "password": f"Pw-{RUN}-long"}).status_code == 200
    r = client.post(f"/staff/{world['B'].id}/reset-password", headers=admin)
    assert r.status_code == 200 and len(r.json()["password"]) == 12
    assert client.post(f"/staff/{uuid.uuid4()}/reset-password", headers=admin).status_code == 404


def test_last_admin_cannot_be_disabled(client, world, monkeypatch):
    admin = token(world, "admin")
    import routers.staff as staff_router

    async def none(db, user_id):
        return 0

    monkeypatch.setattr(staff_router, "_other_active_admins", none)
    for payload in ({"is_active": False}, {"roles": ["teacher"]}):
        r = client.patch(f"/staff/{world['admin2'].id}", headers=admin, json=payload)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "last_admin"
    monkeypatch.undo()
    # With other admins present, an admin can be turned into a teacher and back.
    assert client.patch(f"/staff/{world['admin2'].id}", headers=admin, json={"roles": ["teacher"]}).status_code == 200
    assert client.get("/staff", headers=token(world, "admin2")).status_code == 403
    assert client.patch(f"/staff/{world['admin2'].id}", headers=admin, json={"roles": ["admin"]}).status_code == 200


# ---------------------------------------------------------------- 9. student passwords: view, reset, unlock
def test_password_view_reset_and_unlock(client, world, caplog):
    a, b, m, admin = token(world, "A"), token(world, "B"), token(world, "M"), token(world, "admin")
    sid = world["SA1"].id
    old = run(client, _account, sid)
    r = client.post(f"/students/{sid}/access/reset-password", headers=a)
    assert r.status_code == 200
    password = r.json()["password"]
    new = run(client, _account, sid)
    assert new.password_hash != old.password_hash and new.password_enc and new.password_enc != old.password_enc
    assert verify_password(password, new.password_hash) and decrypt_password(new.password_enc) == password
    assert password not in new.password_enc

    with caplog.at_level(logging.INFO, logger="itdeti.student_access"):
        r = client.get(f"/students/{sid}/access/password", headers=a)
    assert r.status_code == 200 and r.json()["password"] == password and r.headers["cache-control"] == "no-store"
    assert any(str(sid) in rec.getMessage() and str(world["A"].id) in rec.getMessage() for rec in caplog.records)
    assert all(password not in rec.getMessage() for rec in caplog.records)
    assert client.get(f"/students/{sid}/access/password", headers=admin).json()["password"] == password
    assert client.get(f"/students/{sid}/access/password", headers=b).status_code == 404
    assert client.get(f"/students/{sid}/access/password", headers=m).status_code == 403

    # Night-1 account without password_enc.
    r = client.get(f"/students/{world['SA2'].id}/access/password", headers=a)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "password_not_stored"

    # Unlock.
    for _ in range(5):
        client.post("/student/auth/login", json={"login": f"t2sa1{RUN}", "password": "000000"})
    from routers.student_site import login_limiter
    login_limiter.reset()
    assert client.get(f"/students/{sid}/access", headers=a).json()["locked_until"] is not None
    assert client.post(f"/students/{sid}/access/unlock", headers=b).status_code == 404
    r = client.post(f"/students/{sid}/access/unlock", headers=a)
    assert r.status_code == 200 and r.json()["locked_until"] is None and r.json()["failed_attempts"] == 0
    assert client.post("/student/auth/login", json={"login": f"t2sa1{RUN}", "password": password}).status_code == 200
    login_limiter.reset()

    # Teacher may not create or switch off access, nor get a login suggestion.
    assert client.patch(f"/students/{sid}/access", headers=a, json={"is_active": False}).status_code == 403
    assert client.post(f"/students/{world['SN'].id}/access", headers=a, json={}).status_code == 403
    assert client.get(f"/students/{sid}/access/login-suggestion", headers=a).status_code == 403


def test_password_view_without_key(client, world, monkeypatch):
    monkeypatch.setattr(settings, "STUDENT_PASSWORD_KEY", None)
    a = token(world, "A")
    sid = world["SA1"].id
    r = client.get(f"/students/{sid}/access/password", headers=a)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "password_view_disabled"
    r = client.post(f"/students/{sid}/access/reset-password", headers=a)
    assert r.status_code == 200  # reset still works, the password is shown once
    assert run(client, _account, sid).password_enc is None
