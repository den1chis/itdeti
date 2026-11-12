"""Night 5: staff on the learning platform (part A) and staff management in the CRM (part B).

Integration tests against the DEV database (.env), like the other night tests: every run creates its own uniquely
named rows and deletes them at the end. Refuses to run against a non-dev database."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select, update

from core.config import settings
from scripts.devguard import is_dev_database

if not is_dev_database(settings.DATABASE_URL):  # pragma: no cover
    pytest.skip("integration tests run only against the dev database", allow_module_level=True)

import main  # noqa: E402
from core import access, handoff, login_guard  # noqa: E402
from core.database import AsyncSessionLocal  # noqa: E402
from core.security import create_access_token, create_refresh_token, hash_password  # noqa: E402
from core.student_auth import create_student_token  # noqa: E402
from models.lesson import Lesson  # noqa: E402
from models.lms import LmsCourse, LmsEnrollment, LmsLesson, LmsLessonProgress, LmsModule, LmsStudentNote  # noqa: E402
from models.parent import Parent, StudentParent  # noqa: E402
from models.refresh_token import RefreshToken  # noqa: E402
from models.schedule import Event, StudentScheduleSlot  # noqa: E402
from models.security import AuditLog, AuthEmailLock, AuthHandoffCode, AuthLoginState  # noqa: E402
from models.student import Student  # noqa: E402
from models.student_account import StudentAccount  # noqa: E402
from models.user import User  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUN = uuid.uuid4().hex[:6]
PASSWORD = "pw5-" + RUN
NOW = datetime.now(timezone.utc)
IP_PREFIX = f"2001:db8:5{int(RUN[:3], 16) % 0x1000:03x}:{int(RUN[3:], 16) | 0x1000:x}"
MONEY_KEYS = {"balance", "lesson_price", "price", "amount", "payments", "balance_deducted", "current_month_due",
              "monthly_debt", "payments_this_month", "remaining_lessons", "income", "expenses", "total"}
CONTACT_KEYS = {"parents", "phone", "whatsapp_phone", "email"}

USERS = {"admin": ["admin"], "A": ["teacher"], "B": ["teacher"], "M": ["methodist"], "TM": ["teacher", "methodist"],
         "X": ["parent"], "D": ["teacher"], "T": ["teacher"]}
STUDENTS = {"SA1": "A", "SA2": "A", "SB1": "B", "SB2": "B", "SN": None, "SD": "D"}


def ip(n: int) -> str:
    value = f"{IP_PREFIX}::{n:x}"
    assert str(__import__("ipaddress").ip_address(value)) == value, value
    return value


def email_of(key):
    return f"t5-{key.lower()}-{RUN}@example.com"


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


async def _create_world():
    w = {"extra_users": []}
    async with AsyncSessionLocal() as db:
        hashed = hash_password(PASSWORD)
        for key, roles in USERS.items():
            w[key] = User(email=email_of(key), full_name=f"t5 {key} {RUN}", role=roles[0], roles=roles,
                          is_active=True, password_hash=hashed)
            db.add(w[key])
        await db.flush()
        for key, owner in STUDENTS.items():
            w[key] = Student(full_name=f"Т5{key} {RUN}", lesson_price=Decimal("1000"), balance=Decimal("5000"),
                             is_active=True, course=f"Курс {key}", teacher_id=w[owner].id if owner else None)
            db.add(w[key])
        w["course"] = LmsCourse(title=f"pytest night5 {RUN}", course_type="code")
        db.add(w["course"])
        await db.flush()
        w["module"] = LmsModule(course_id=w["course"].id, title="m5", sort_order=0)
        db.add(w["module"])
        await db.flush()
        w["l1"] = LmsLesson(module_id=w["module"].id, title="l5-1", sort_order=0)
        w["l2"] = LmsLesson(module_id=w["module"].id, title="l5-2", sort_order=1)
        db.add_all([w["l1"], w["l2"]])
        await db.flush()
        for key in STUDENTS:
            db.add(LmsEnrollment(student_id=w[key].id, course_id=w["course"].id, status="active"))
        db.add(LmsLessonProgress(student_id=w["SA1"].id, lesson_id=w["l1"].id, status="done"))
        w["parent"] = Parent(full_name=f"Родитель5 {RUN}", phone="+70000005555")
        db.add(w["parent"])
        await db.flush()
        db.add(StudentParent(student_id=w["SA1"].id, parent_id=w["parent"].id))
        db.add(StudentAccount(student_id=w["SA1"].id, login=f"t5sa1{RUN}", is_active=True, password_hash=hash_password("123456")))
        # one future lesson of SA1 (teacher A) for the read-only schedule
        event = Event(title=f"t5 {RUN}", event_type="personal", start_time=NOW + timedelta(days=1),
                      end_time=NOW + timedelta(days=1, hours=1))
        db.add(event)
        await db.flush()
        w["lesson_a"] = Lesson(student_id=w["SA1"].id, event_id=event.id, lesson_kind="lesson", lesson_type="regular",
                               status="scheduled", price=Decimal("1000"))
        db.add(w["lesson_a"])
        await db.commit()
    return w


async def _cleanup(w):
    async with AsyncSessionLocal() as db:
        student_ids = [w[k].id for k in STUDENTS] + list(w.get("extra_students", []))
        user_ids = [w[k].id for k in USERS] + list(w["extra_users"])
        event_ids = (await db.execute(select(Lesson.event_id).where(Lesson.student_id.in_(student_ids)))).scalars().all()
        await db.execute(delete(Lesson).where(Lesson.student_id.in_(student_ids)))
        await db.execute(delete(Event).where(Event.id.in_([e for e in event_ids if e])))
        await db.execute(delete(StudentScheduleSlot).where(StudentScheduleSlot.student_id.in_(student_ids)))
        await db.execute(delete(LmsStudentNote).where(LmsStudentNote.student_id.in_(student_ids)))
        await db.execute(delete(LmsLessonProgress).where(LmsLessonProgress.student_id.in_(student_ids)))
        await db.execute(delete(LmsEnrollment).where(LmsEnrollment.student_id.in_(student_ids)))
        await db.execute(delete(StudentAccount).where(StudentAccount.student_id.in_(student_ids)))
        await db.execute(delete(StudentParent).where(StudentParent.student_id.in_(student_ids)))
        await db.execute(delete(Parent).where(Parent.id == w["parent"].id))
        await db.execute(delete(LmsLesson).where(LmsLesson.module_id == w["module"].id))
        await db.execute(delete(LmsModule).where(LmsModule.course_id == w["course"].id))
        await db.execute(delete(LmsCourse).where(LmsCourse.id == w["course"].id))
        await db.execute(delete(Student).where(Student.id.in_(student_ids)))
        await db.execute(delete(AuthHandoffCode).where(AuthHandoffCode.user_id.in_(user_ids)))
        await db.execute(delete(AuthLoginState).where(or_(AuthLoginState.email.like(f"%{RUN}%"), AuthLoginState.ip.like(f"{IP_PREFIX}%"))))
        await db.execute(delete(AuthEmailLock).where(AuthEmailLock.email.like(f"%{RUN}%")))
        await db.execute(delete(AuditLog).where(or_(AuditLog.actor_user_id.in_(user_ids), AuditLog.target_id.in_([str(u) for u in user_ids]),
                                                    AuditLog.details["email"].astext.like(f"%{RUN}%"))))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


@pytest.fixture(scope="module")
def world(client):
    w = run(client, _create_world)
    yield w
    run(client, _cleanup, w)


@pytest.fixture()
def proxied(monkeypatch):
    """Behind Cloudflare (CF-Connecting-IP is the client address); in-memory limiters start empty."""
    monkeypatch.setattr(settings, "BEHIND_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    for limiter in (login_guard.ip_limiter, handoff.redeem_limiter, handoff.issue_limiter):
        limiter.reset()
    yield
    for limiter in (login_guard.ip_limiter, handoff.redeem_limiter, handoff.issue_limiter):
        limiter.reset()


def tok(w, key):
    return bearer(create_access_token(str(w[key].id), USERS[key][0]))


def student_tok(w, key):
    return bearer(create_student_token(w[key].id))


async def _rows(model, *conditions):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(model).where(*conditions))).scalars().all()


async def _exec(statement):
    async with AsyncSessionLocal() as db:
        await db.execute(statement)
        await db.commit()


def login(client, key, password=PASSWORD, from_ip=None):
    headers = {"CF-Connecting-IP": from_ip} if from_ip else {}
    return client.post("/auth/login", json={"email": email_of(key), "password": password}, headers=headers)


def issue(client, w, key, target="profile", headers=None):
    return client.post("/auth/handoff", json={"target": target}, headers=headers or tok(w, key))


def redeem(client, code, from_ip=None):
    headers = {"CF-Connecting-IP": from_ip} if from_ip else {}
    return client.post("/auth/handoff/redeem", json={"code": code}, headers=headers)


# ================================================================ A1. one login screen
def test_platform_staff_login_uses_crm_login_and_reports_roles(client, world, proxied):
    r = login(client, "TM", from_ip=ip(1))
    assert r.status_code == 200
    me = client.get("/auth/me", headers=bearer(r.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["roles"] == ["teacher", "methodist"] and me.json()["role"] == "teacher"
    # Android/CRM fields are still there
    assert {"id", "email", "role", "full_name", "phone"} <= set(me.json())


def test_platform_login_refuses_users_without_staff_roles(client, world, proxied):
    r = login(client, "X", from_ip=ip(2))
    assert r.status_code == 200  # /auth/login itself is unchanged (Android, CRM)
    access_token = bearer(r.json()["access_token"])
    assert client.get("/auth/me", headers=access_token).status_code == 403
    assert client.post("/auth/handoff", json={"target": "catalog"}, headers=access_token).status_code == 403
    assert client.get("/lms/courses", headers=access_token).status_code == 403


def test_platform_and_crm_share_the_login_lock(client, world, proxied):
    """The platform posts to the same /auth/login as the CRM: 5 wrong passwords lock the pair email+IP for both."""
    source = (ROOT.parent / "itdetiLearn" / "app.js").read_text(encoding="utf-8")
    assert "api('/auth/login'" in source and "staff_access" in source
    origin = {"Origin": "https://den1chis.github.io"}
    for _ in range(5):
        r = client.post("/auth/login", json={"email": email_of("B"), "password": "wrong-" + RUN},
                        headers={"CF-Connecting-IP": ip(3), **origin})
        assert r.status_code in (401, 429)
    r = login(client, "B", from_ip=ip(3))  # the right password, "from the CRM"
    assert r.status_code == 429 and "минут" in r.json()["detail"]


def test_inactive_staff_cannot_log_in_on_the_platform(client, world, proxied):
    run(client, _exec, update(User).where(User.id == world["T"].id).values(is_active=False))
    try:
        assert login(client, "T", from_ip=ip(4)).status_code == 401
    finally:
        run(client, _exec, update(User).where(User.id == world["T"].id).values(is_active=True))


# ================================================================ A2. staff mode
def test_staff_token_never_writes_student_progress(client, world):
    for key in ("admin", "A", "M"):
        r = client.post(f"/student/lessons/{world['l2'].id}/progress", json={"status": "done"}, headers=tok(world, key))
        assert r.status_code == 401
    refresh = bearer(create_refresh_token(str(world["A"].id)))
    assert client.post(f"/student/lessons/{world['l2'].id}/progress", json={"status": "done"}, headers=refresh).status_code == 401
    rows = run(client, _rows, LmsLessonProgress, LmsLessonProgress.lesson_id == world["l2"].id)
    assert rows == []


def test_student_token_opens_no_staff_route(client, world):
    s = student_tok(world, "SA1")
    for method, url in (("GET", "/teacher/learning/students"), ("GET", f"/teacher/learning/students/{world['SA1'].id}"),
                        ("GET", "/teacher/students"), ("GET", "/teacher/lessons"), ("GET", "/lms/courses"),
                        ("GET", f"/lms/lessons/{world['l1'].id}"), ("GET", "/auth/me"), ("POST", "/auth/handoff")):
        r = client.request(method, url, headers=s, json={"target": "catalog"})
        assert r.status_code == 401, f"{method} {url} -> {r.status_code}"


def test_catalog_is_open_to_every_staff_role(client, world):
    for key in ("admin", "A", "M", "TM"):
        assert client.get("/lms/courses", headers=tok(world, key)).status_code == 200
        assert client.get(f"/lms/courses/{world['course'].id}", headers=tok(world, key)).status_code == 200
        assert client.get(f"/lms/lessons/{world['l1'].id}", headers=tok(world, key)).status_code == 200


def test_learning_students_teacher_sees_only_own(client, world):
    r = client.get("/teacher/learning/students", headers=tok(world, "A"))
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()}
    assert ids == {str(world["SA1"].id), str(world["SA2"].id)}
    sa1 = next(s for s in r.json() if s["id"] == str(world["SA1"].id))
    course = next(c for c in sa1["courses"] if c["course_id"] == str(world["course"].id))
    assert course["lessons_total"] == 2 and course["lessons_done"] == 1 and course["modules"] is None
    # B's students: not in any answer, detail is 404
    for key in ("SB1", "SB2", "SN"):
        assert client.get(f"/teacher/learning/students/{world[key].id}", headers=tok(world, "A")).status_code == 404
    detail = client.get(f"/teacher/learning/students/{world['SA1'].id}", headers=tok(world, "A")).json()
    statuses = {l["id"]: l["status"] for c in detail["courses"] for m in c["modules"] for l in m["lessons"]}
    assert statuses == {str(world["l1"].id): "done", str(world["l2"].id): "not_started"}


def test_learning_students_admin_sees_all_methodist_none(client, world):
    ids = {s["id"] for s in client.get("/teacher/learning/students", headers=tok(world, "admin")).json()}
    assert {str(world[k].id) for k in STUDENTS} <= ids
    assert client.get(f"/teacher/learning/students/{world['SB1'].id}", headers=tok(world, "admin")).status_code == 200
    assert client.get("/teacher/learning/students", headers=tok(world, "M")).status_code == 403
    tm = client.get("/teacher/learning/students", headers=tok(world, "TM"))
    assert tm.status_code == 200 and tm.json() == []


def test_staff_mode_answers_have_no_money_and_no_contacts(client, world):
    answers = {
        "learning list": client.get("/teacher/learning/students", headers=tok(world, "A")).json(),
        "learning detail": client.get(f"/teacher/learning/students/{world['SA1'].id}", headers=tok(world, "A")).json(),
        "learning admin": client.get("/teacher/learning/students", headers=tok(world, "admin")).json(),
        "me": client.get("/auth/me", headers=tok(world, "A")).json(),
        "courses": client.get("/lms/courses", headers=tok(world, "A")).json(),
        "course": client.get(f"/lms/courses/{world['course'].id}", headers=tok(world, "A")).json(),
        "lesson": client.get(f"/lms/lessons/{world['l1'].id}", headers=tok(world, "A")).json(),
    }
    for name, body in answers.items():
        keys = set(_all_keys(body))
        assert not keys & MONEY_KEYS, f"{name}: money keys {keys & MONEY_KEYS}"
        if name.startswith("learning"):
            assert not keys & CONTACT_KEYS, f"{name}: contact keys {keys & CONTACT_KEYS}"
            assert "+70000005555" not in str(body)


# ================================================================ A3. handoff
def test_handoff_success_gives_a_new_token_pair_once(client, world, proxied):
    crm = login(client, "A", from_ip=ip(10)).json()
    r = client.post("/auth/handoff", json={"target": f"student:{world['SA1'].id}"}, headers=bearer(crm["access_token"]))
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    code = r.json()["code"]
    assert len(code) >= 43 and r.json()["expires_in"] == 60
    # only the hash is stored
    stored = run(client, _rows, AuthHandoffCode, AuthHandoffCode.user_id == world["A"].id)
    assert stored and all(row.code_hash != code and code not in row.code_hash for row in stored)
    assert any(row.code_hash == handoff.code_hash(code) for row in stored)

    first = redeem(client, code, ip(11))
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["target"] == f"student:{world['SA1'].id}"
    assert data["access_token"] != crm["access_token"] and data["refresh_token"] != crm["refresh_token"]
    assert client.get("/auth/me", headers=bearer(data["access_token"])).json()["id"] == str(world["A"].id)
    assert client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]}).status_code == 200
    # the CRM session is untouched
    assert client.get("/auth/me", headers=bearer(crm["access_token"])).status_code == 200

    again = redeem(client, code, ip(11))
    assert again.status_code == 401 and again.json()["detail"]["code"] == "invalid_code"
    rows = run(client, _rows, AuditLog, AuditLog.actor_user_id == world["A"].id,
               AuditLog.action.in_([audit_code for audit_code in ("handoff_issued", "handoff_used")]))
    assert {row.action for row in rows} >= {"handoff_issued", "handoff_used"}
    for row in rows:
        assert code not in str(row.details) and "token" not in str(row.details)


def test_handoff_expired_code_is_refused(client, world, proxied):
    code = issue(client, world, "A", "catalog").json()["code"]
    run(client, _exec, update(AuthHandoffCode).where(AuthHandoffCode.code_hash == handoff.code_hash(code))
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    r = redeem(client, code, ip(12))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_code"
    # and it is gone (one try only)
    assert run(client, _rows, AuthHandoffCode, AuthHandoffCode.code_hash == handoff.code_hash(code)) == []


def test_handoff_expired_rows_are_deleted_when_new_codes_are_issued(client, world, proxied):
    run(client, _exec, AuthHandoffCode.__table__.insert().values(
        id=uuid.uuid4(), code_hash="old-" + RUN, user_id=world["B"].id, target="catalog",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
    assert issue(client, world, "B", "catalog").status_code == 201
    assert run(client, _rows, AuthHandoffCode, AuthHandoffCode.code_hash == "old-" + RUN) == []


def test_handoff_foreign_student_target_is_404(client, world, proxied):
    assert issue(client, world, "A", f"student:{world['SB1'].id}").status_code == 404
    assert issue(client, world, "A", f"student:{world['SN'].id}").status_code == 404
    assert issue(client, world, "M", f"student:{world['SA1'].id}").status_code == 404
    assert issue(client, world, "admin", f"student:{world['SB1'].id}").status_code == 201
    for bad in ("", "home", "student:1", "student:../x", "catalog; drop", "https://evil.example"):
        assert issue(client, world, "A", bad).status_code in (400, 422), bad


def test_handoff_refused_after_deactivation_or_role_loss(client, world, proxied):
    code = issue(client, world, "T", "catalog").json()["code"]
    run(client, _exec, update(User).where(User.id == world["T"].id).values(is_active=False))
    try:
        assert redeem(client, code, ip(13)).status_code == 401
    finally:
        run(client, _exec, update(User).where(User.id == world["T"].id).values(is_active=True))
    code = issue(client, world, "T", "catalog").json()["code"]
    run(client, _exec, update(User).where(User.id == world["T"].id).values(roles=["parent"], role="parent"))
    try:
        assert redeem(client, code, ip(13)).status_code == 401
    finally:
        run(client, _exec, update(User).where(User.id == world["T"].id).values(roles=["teacher"], role="teacher"))


def test_handoff_cannot_be_issued_by_students_refresh_tokens_or_anonymous(client, world, proxied):
    assert client.post("/auth/handoff", json={"target": "catalog"}, headers=student_tok(world, "SA1")).status_code == 401
    refresh = bearer(create_refresh_token(str(world["A"].id)))
    assert client.post("/auth/handoff", json={"target": "catalog"}, headers=refresh).status_code == 401
    assert client.post("/auth/handoff", json={"target": "catalog"}).status_code in (401, 403)
    # a student token is not a code either
    assert redeem(client, create_student_token(world["SA1"].id), ip(14)).status_code == 401


def test_handoff_redeem_is_limited_per_ip(client, world, proxied):
    for n in range(10):
        assert redeem(client, "x" * 43, ip(20)).status_code == 401
    r = redeem(client, "x" * 43, ip(20))
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert redeem(client, "x" * 43, ip(21)).status_code == 401  # another address has its own bucket


def test_handoff_routes_are_classified():
    assert ("POST", "/auth/handoff/redeem") in access.PUBLIC_ROUTES
    route = next(r for r in main.app.routes if isinstance(r, APIRoute) and r.path == "/auth/handoff")
    assert access.access_label(route) == "roles: admin, teacher, methodist"
    route = next(r for r in main.app.routes if isinstance(r, APIRoute) and r.path == "/teacher/learning/students")
    assert access.access_label(route) == "roles: admin, teacher"


def test_migration_015_is_additive_with_rls():
    from scripts import prod_ops
    from scripts.apply_migration import static_problems

    sql = (ROOT / "supabase" / "migrations" / "015_staff_handoff.sql").read_text(encoding="utf-8")
    assert static_problems(sql) == []
    assert prod_ops.migration_problems(sql) == []
    assert prod_ops.resolve_migration("015_staff_handoff.sql").name == "015_staff_handoff.sql"
    assert "ENABLE ROW LEVEL SECURITY" in sql and not re.search(r"\bDROP\b|\bDELETE\s+FROM\b|\bRENAME\b", sql, re.I)
