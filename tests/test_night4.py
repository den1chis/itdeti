"""Night 4: CF-Connecting-IP, CRM login throttling, audit log, hashed refresh tokens, scripts/prod_ops.py.

Unit tests need no database. Integration tests use the DEV database (.env) only: every run creates its own
uniquely named rows and deletes them at the end. prod_ops.py is exercised with --dev-check, never with --prod
against a real database (the --prod branches are tested with fake secrets files that never get connected)."""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, or_, select, text
from starlette.requests import Request

from core import audit, login_guard, refresh_tokens
from core.client_ip import client_ip
from core.config import settings
from scripts import prod_ops
from scripts.devguard import is_dev_database

ROOT = Path(__file__).resolve().parent.parent
RUN = uuid.uuid4().hex[:6]
PASSWORD = "pw-" + RUN
# Unique "client addresses" of this run (TEST-NET-2 198.51.100.0/24 is used by night 3; 203.0.113.0/24 here
# with a run-specific third octet would leave the documentation range, so IPv6 documentation space is used).
IP_PREFIX = f"2001:db8:{RUN[:4]}:{RUN[4:]}"


def ip(n: int) -> str:
    return f"{IP_PREFIX}::{n:x}"


def _request(socket_ip="10.0.0.1", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or [])]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw, "client": (socket_ip, 1234)})


# ================================================================ 2.1 CF-Connecting-IP (unit)
def test_cf_connecting_ip_wins_over_forwarded_for():
    r = _request("10.0.0.1", [("CF-Connecting-IP", "203.0.113.7"), ("X-Forwarded-For", "6.6.6.6, 10.9.9.9")])
    assert client_ip(r, behind_proxy=True, trusted_hops=1) == "203.0.113.7"
    r = _request("10.0.0.1", [("CF-Connecting-IP", "2001:db8::1")])
    assert client_ip(r, behind_proxy=True) == "2001:db8::1"


def test_cf_connecting_ip_ignored_without_behind_proxy():
    r = _request("10.0.0.1", [("CF-Connecting-IP", "203.0.113.7"), ("X-Forwarded-For", "6.6.6.6")])
    assert client_ip(r, behind_proxy=False) == "10.0.0.1"


def test_bad_cf_connecting_ip_falls_back_to_forwarded_for_then_socket():
    r = _request("10.0.0.1", [("CF-Connecting-IP", "not-an-ip"), ("X-Forwarded-For", "6.6.6.6, 203.0.113.9")])
    assert client_ip(r, behind_proxy=True, trusted_hops=1) == "203.0.113.9"
    r = _request("10.0.0.1", [("CF-Connecting-IP", "1.1.1.1, 2.2.2.2")])  # a list is not what Cloudflare sends
    assert client_ip(r, behind_proxy=True, trusted_hops=1) == "10.0.0.1"
    r = _request("10.0.0.1", [("CF-Connecting-IP", "1.1.1.1"), ("CF-Connecting-IP", "2.2.2.2"),
                              ("X-Forwarded-For", "203.0.113.9")])  # two headers -> not trusted
    assert client_ip(r, behind_proxy=True, trusted_hops=1) == "203.0.113.9"
    assert client_ip(_request("10.0.0.1", [("CF-Connecting-IP", "")]), behind_proxy=True) == "10.0.0.1"


# ================================================================ unit: messages, hashing, audit cleaning
def test_lock_message_is_russian_with_minutes():
    assert login_guard.minutes_text(900) == "Слишком много попыток входа. Попробуйте через 15 минут."
    assert login_guard.minutes_text(1) == "Слишком много попыток входа. Попробуйте через 1 минуту."
    assert login_guard.minutes_text(61) == "Слишком много попыток входа. Попробуйте через 2 минуты."
    assert login_guard.minutes_text(11 * 60) == "Слишком много попыток входа. Попробуйте через 11 минут."


def test_email_is_normalized():
    assert login_guard.normalize_email("  Owner@Example.COM ") == "owner@example.com"


def test_refresh_token_row_holds_only_the_hash():
    token = "header.payload.signature"
    row = refresh_tokens.new_row(uuid.uuid4(), token)
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert row.token_hash == digest and row.token == "sha256:" + digest
    assert token not in row.token


def test_refresh_tokens_differ_within_one_second():
    from core.security import create_refresh_token, decode_token

    user_id = str(uuid.uuid4())
    a, b = create_refresh_token(user_id), create_refresh_token(user_id)
    assert a != b and decode_token(a)["sub"] == user_id and decode_token(a)["type"] == "refresh"


def test_audit_details_never_keep_secrets():
    clean = audit._clean({"email": "a@b.c", "password": "x", "new_password": "y", "refresh_token": "t",
                          "STUDENT_PASSWORD_KEY": "k", "password_hash": "h", "roles": ["admin"], "amount": 5})
    assert clean == {"email": "a@b.c", "roles": ["admin"], "amount": 5}


def test_health_reports_version():
    import main
    from core.version import APP_VERSION

    body = TestClient(main.app).get("/health").json()
    assert body["status"] == "ok" and body["version"] == APP_VERSION and "commit" in body


# ================================================================ unit: prod_ops (no database)
PROD_LIKE_URL = "postgresql+asyncpg://postgres.abcdefghijklmnopqrst:S3cr3t-Pa55@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"


def _secrets(tmp_path, content):
    path = tmp_path / "prod.env"
    path.write_text(content, encoding="utf-8")
    return path


def test_prod_ops_needs_exactly_one_target(tmp_path):
    with pytest.raises(prod_ops.Refused):
        prod_ops.resolve_target(False, False, tmp_path / "none.env")
    with pytest.raises(prod_ops.Refused):
        prod_ops.resolve_target(True, True, tmp_path / "none.env")


def test_prod_ops_refuses_missing_or_empty_secret(tmp_path):
    with pytest.raises(prod_ops.Refused, match="not found"):
        prod_ops.resolve_target(True, False, tmp_path / "none.env")
    with pytest.raises(prod_ops.Refused, match="not set"):
        prod_ops.resolve_target(True, False, _secrets(tmp_path, "OTHER=1\nPROD_DATABASE_URL=\n"))


def test_prod_ops_refuses_dev_and_local_urls_as_prod(tmp_path):
    for url in (
        "postgresql+asyncpg://postgres.yctyhgbhqsbawgjaiock:pw@aws-0-eu-central-1.pooler.supabase.com:6543/postgres",
        "postgresql://postgres:pw@db.yctyhgbhqsbawgjaiock.supabase.co:5432/postgres",
        "postgresql://postgres:pw@localhost:5432/postgres",
        settings.DATABASE_URL if is_dev_database(settings.DATABASE_URL) else "postgresql://u:p@127.0.0.1/x",
    ):
        with pytest.raises(prod_ops.Refused, match="DEV"):
            prod_ops.resolve_target(True, False, _secrets(tmp_path, f"PROD_DATABASE_URL={url}\n"))


def test_prod_ops_accepts_both_url_forms_and_masks_the_target(tmp_path):
    dsn, label = prod_ops.resolve_target(True, False, _secrets(tmp_path, f'PROD_DATABASE_URL="{PROD_LIKE_URL}"\n'))
    assert dsn.startswith("postgresql://postgres.abcdefghijklmnopqrst:")
    assert label.startswith("PROD")
    for secret in ("S3cr3t-Pa55", "abcdefghijklmnopqrst", "aws-0-eu-central-1.pooler.supabase.com"):
        assert secret not in label
    assert prod_ops.to_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    with pytest.raises(prod_ops.Refused):
        prod_ops.to_dsn("mysql://u:p@h/db")


def test_prod_ops_cli_never_prints_the_url(tmp_path, capsys):
    url = "postgresql+asyncpg://postgres.yctyhgbhqsbawgjaiock:Very-Secret-1@aws-0-x.pooler.supabase.com:6543/postgres"
    code = prod_ops.main(["--prod", "--secrets", str(_secrets(tmp_path, f"PROD_DATABASE_URL={url}\n")), "counts"])
    out = capsys.readouterr().out
    assert code == 2 and "ОТКАЗ" in out
    assert "Very-Secret-1" not in out and "yctyhgbhqsbawgjaiock" not in out
    assert prod_ops.main(["counts"]) == 2  # no target at all


def test_prod_ops_encodes_special_characters_in_the_password():
    for raw in ("Pa/ss", "Pa?ss", "Pa#ss", "P@ss/w?o#rd"):
        dsn = prod_ops.to_dsn(f"postgresql+asyncpg://postgres.abcdefghijklmnopqrst:{raw}@db.example.com:6543/postgres")
        parsed = __import__("urllib.parse").parse.urlparse(dsn)
        assert parsed.hostname == "db.example.com" and parsed.port == 6543 and parsed.path == "/postgres"
        assert __import__("urllib.parse").parse.unquote(parsed.password) == raw
    # An already encoded password is not encoded twice.
    assert prod_ops.to_dsn("postgresql://u:Pa%2Fss@h:5432/db") == "postgresql://u:Pa%2Fss@h:5432/db"
    prod_ops.to_dsn("postgresql://u:SECRETX/1@h/db")       # redact() knows the passwords to_dsn() has seen
    assert "SECRETX" not in prod_ops.redact("boom postgresql://u:SECRETX%2F1@h/db SECRETX/1")


def test_prod_ops_connection_error_never_prints_the_password(tmp_path, capsys):
    # 127.0.0.2:1 refuses at once; the password has every character that used to break URL parsing.
    password = "Ab/cd?ef#gh-SECRETX"
    url = f"postgresql://postgres.abcdefghijklmnopqrst:{password}@127.0.0.2:1/postgres"
    code = prod_ops.main(["--prod", "--secrets", str(_secrets(tmp_path, f"PROD_DATABASE_URL={url}\n")), "counts"])
    out = capsys.readouterr()
    text = out.out + out.err
    assert code == 2 and "ОШИБКА" in text
    for piece in ("SECRETX", "Ab/cd", "Ab%2Fcd", "abcdefghijklmnopqrst"):
        assert piece not in text


def test_prod_ops_checks_format_and_rules():
    checks = prod_ops.parse_checks(
        "-- header\n\n-- [1] first\n-- note\n-- @expect scalar >= 2\nSELECT count(*)\nFROM t;\n\n"
        "-- [2] second\n-- @expect info\nSELECT 1;\n"
    )
    assert [(c["n"], c["title"], c["expect"]) for c in checks] == [(1, "first", "scalar >= 2"), (2, "second", "info")]
    assert checks[0]["sql"] == "SELECT count(*)\nFROM t"
    with pytest.raises(prod_ops.Refused):
        prod_ops.parse_checks("-- [1] no rule\nSELECT 1;\n")
    for bad in ("DELETE FROM users", "SELECT 1; DROP TABLE users", "UPDATE users SET role = 'admin'"):
        with pytest.raises(prod_ops.Refused):
            prod_ops.parse_checks(f"-- [1] bad\n-- @expect info\n{bad};\n")
    ev = prod_ops.evaluate
    assert ev("info", []) and ev("all_true", [(True, True)]) and not ev("all_true", [(True, False)])
    assert not ev("all_true", []) and not ev("all_true", [(True, None)])
    assert ev("rows == 0", []) and not ev("rows == 0", [(1,)]) and ev("rows >= 1", [(1,)])
    assert ev("scalar == 3", [(3,)]) and not ev("scalar == 3", [(2,)]) and not ev("scalar == 3", [])
    assert ev("scalar >= 25", [(36,)]) and not ev("scalar >= 25", [(5,)]) and not ev("scalar >= 1", [(None,)])
    assert ev("scalar == true", [(True,)]) and not ev("scalar == true", [(False,)])
    with pytest.raises(prod_ops.Refused):
        ev("scalar < 3", [(1,)])


def test_prod_ops_check_files_are_read_only_selects():
    for name in ("prod_preflight.sql", "prod_postcheck_014.sql"):
        checks = prod_ops.parse_checks((ROOT / "scripts" / name).read_text(encoding="utf-8"))
        assert len(checks) >= 8
        assert [c["n"] for c in checks] == list(range(1, len(checks) + 1))


def test_migration_014_is_additive_and_forbidden_statements_are_caught():
    sql = (ROOT / "supabase" / "migrations" / "014_security.sql").read_text(encoding="utf-8")
    assert prod_ops.migration_problems(sql) == []
    ok = ("CREATE TABLE IF NOT EXISTS public.t (id UUID PRIMARY KEY, s UUID REFERENCES students(id) ON DELETE CASCADE);\n"
          "ALTER TABLE public.t ENABLE ROW LEVEL SECURITY;\n"
          "ALTER TABLE users ADD COLUMN IF NOT EXISTS nick TEXT NULL;\n"
          "UPDATE users SET nick = lower(email) WHERE nick IS NULL;\n")
    assert prod_ops.migration_problems(ok) == []
    bad = {
        "DROP TABLE students;": "DROP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS x INT; DELETE FROM users;": "DELETE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS x INT; TRUNCATE payments;": "TRUNCATE",
        "ALTER TABLE users RENAME COLUMN role TO r;": "RENAME",
        "ALTER TABLE users ALTER COLUMN email TYPE VARCHAR(10);": "ALTER COLUMN",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS x INT; UPDATE users SET role = 'admin' WHERE x IS NULL;": "existing columns",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS x INT; UPDATE users SET x = 1;": "no WHERE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS x INT; UPDATE users SET x = 1, email = 'a' WHERE x IS NULL;": "existing columns",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS x INT; INSERT INTO users (email) VALUES ('a');": "INSERT",
        "CREATE TABLE IF NOT EXISTS public.naked (id INT);": "ROW LEVEL SECURITY",
        "ALTER TABLE users DISABLE ROW LEVEL SECURITY;": "DISABLE ROW LEVEL SECURITY",
        "SELECT 1;": "nothing to apply",
    }
    for statement, marker in bad.items():
        problems = prod_ops.migration_problems(statement)
        assert any(marker in p for p in problems), (statement, problems)
    # Words inside comments and string literals are not statements.
    assert prod_ops.migration_problems(
        "-- DROP TABLE x\nALTER TABLE users ADD COLUMN IF NOT EXISTS y TEXT NULL DEFAULT 'DELETE me; DROP it';\n") == []


def test_prod_ops_migrate_takes_only_new_files_from_migrations_dir(tmp_path):
    assert prod_ops.resolve_migration("014_security.sql").name == "014_security.sql"
    for arg in ("013_access_model.sql", "001_reset_school_schema.sql", "012_student_accounts.sql", "nope.sql",
                "../scripts/prod_backup.sql"):
        with pytest.raises(prod_ops.Refused):
            prod_ops.resolve_migration(arg)
    outside = tmp_path / "015_evil.sql"
    outside.write_text("CREATE TABLE x (id INT);", encoding="utf-8")
    with pytest.raises(prod_ops.Refused):
        prod_ops.resolve_migration(str(outside))


def test_counts_comparison_rules():
    before = {"students": 10, "payments": 5, "events": 7, "refresh_tokens": 100, "notifications": 1}
    assert prod_ops.compare_counts(before, dict(before, audit_log=0)) == []
    assert prod_ops.compare_counts(before, dict(before, events=9, notifications=2, refresh_tokens=40)) == []
    assert prod_ops.compare_counts(before, dict(before, students=9)) == ["students: было 10, стало 9"]
    assert prod_ops.compare_counts(before, dict(before, payments=6)) == ["payments: было 5, стало 6"]
    assert prod_ops.compare_counts(before, dict(before, events=6)) == ["events: было 7, стало 6"]
    after = dict(before)
    del after["students"]
    assert prod_ops.compare_counts(before, after) == ["students: таблица исчезла"]


def test_fresh_backup_rules(tmp_path):
    dsn = prod_ops.to_dsn(PROD_LIKE_URL)
    assert prod_ops.fresh_backup(tmp_path, dsn) is None and prod_ops.fresh_backup(tmp_path / "missing", dsn) is None

    def write(name, **override):
        payload = {"verified": True, "fingerprint": prod_ops.fingerprint(dsn), "tables": {"users": {"rows": 0, "data": []}}}
        payload.update(override)
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    other = write("itdeti_backup_2026-01-01_0001.json", fingerprint="another-database")
    assert prod_ops.fresh_backup(tmp_path, dsn) is None           # a dump of another database does not count
    write("itdeti_backup_2026-01-01_0002.json", verified=False)
    (tmp_path / "itdeti_backup_2026-01-01_0003.json").write_text("{broken", encoding="utf-8")
    assert prod_ops.fresh_backup(tmp_path, dsn) is None
    good = write("itdeti_backup_2026-01-01_0004.json")
    assert prod_ops.fresh_backup(tmp_path, dsn) == good
    old = time.time() - 31 * 60
    for path in (good, other):
        os.utime(path, (old, old))
    assert prod_ops.fresh_backup(tmp_path, dsn) is None           # older than 30 minutes


# ================================================================ integration (dev database)
dev_only = pytest.mark.skipif(not is_dev_database(settings.DATABASE_URL), reason="integration tests run only against the dev database")


@pytest.fixture(scope="module")
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def run(client, fn, *args):
    return client.portal.call(fn, *args)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


USER_KEYS = {"admin": ["admin"], "teacher": ["teacher"], "owner": ["admin"], "victim": ["teacher"], "spare": ["teacher"],
             "tok": ["teacher"]}


def email_of(key):
    return f"t4-{key}-{RUN}@example.com"


async def _create_world():
    from core.database import AsyncSessionLocal
    from core.security import hash_password
    from models.student import Student
    from models.user import User

    w = {}
    async with AsyncSessionLocal() as db:
        hashed = hash_password(PASSWORD)
        for key, roles in USER_KEYS.items():
            w[key] = User(email=email_of(key), full_name=f"t4 {key} {RUN}", role=roles[0], roles=roles,
                          is_active=True, password_hash=hashed)
            db.add(w[key])
        w["student"] = Student(full_name=f"Т4 ученик {RUN}", lesson_price=Decimal("1000"), balance=Decimal("0"), is_active=True)
        db.add(w["student"])
        await db.commit()
    w["extra_users"] = []
    return w


async def _cleanup(w):
    from core.database import AsyncSessionLocal
    from models.expense import Expense
    from models.payment import Payment
    from models.refresh_token import RefreshToken
    from models.security import AuditLog, AuthEmailLock, AuthLoginState
    from models.student import Student
    from models.student_account import StudentAccount
    from models.user import User

    user_ids = [w[k].id for k in USER_KEYS] + list(w["extra_users"])
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuthLoginState).where(or_(AuthLoginState.email.like(f"%{RUN}%"), AuthLoginState.ip.like(f"{IP_PREFIX}%"))))
        await db.execute(delete(AuthEmailLock).where(AuthEmailLock.email.like(f"%{RUN}%")))
        await db.execute(delete(AuditLog).where(or_(
            AuditLog.actor_user_id.in_(user_ids), AuditLog.ip.like(f"{IP_PREFIX}%"),
            AuditLog.details["email"].astext.like(f"%{RUN}%"), AuditLog.details["marker"].astext == RUN)))
        await db.execute(delete(Payment).where(Payment.student_id == w["student"].id))
        await db.execute(delete(Expense).where(Expense.description == f"t4 expense {RUN}"))
        await db.execute(delete(StudentAccount).where(StudentAccount.student_id == w["student"].id))
        await db.execute(delete(Student).where(Student.id == w["student"].id))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


@pytest.fixture(scope="module")
def world(client):
    if not is_dev_database(settings.DATABASE_URL):  # pragma: no cover
        pytest.skip("integration tests run only against the dev database")
    w = run(client, _create_world)
    yield w
    run(client, _cleanup, w)


@pytest.fixture()
def proxied(monkeypatch):
    """The app believes it is behind Cloudflare/Render; the in-memory limiters start empty."""
    from routers.student_site import login_limiter

    monkeypatch.setattr(settings, "BEHIND_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    login_guard.ip_limiter.reset()
    login_limiter.reset()
    yield
    login_guard.ip_limiter.reset()
    login_limiter.reset()


def crm_login(client, email, password, from_ip, xff=None):
    headers = {"CF-Connecting-IP": from_ip}
    if xff:
        headers["X-Forwarded-For"] = xff
    return client.post("/auth/login", json={"email": email, "password": password}, headers=headers)


async def _rows(model, *conditions):
    from core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return (await db.execute(select(model).where(*conditions))).scalars().all()


async def _audit_rows(*conditions):
    from models.security import AuditLog

    return await _rows(AuditLog, *conditions)


async def _exec(sql, params=None):
    from core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(text(sql), params or {})
        await db.commit()


def admin_headers(world):
    from core.security import create_access_token

    return bearer(create_access_token(str(world["admin"].id), "admin"))


# ---------------------------------------------------------------- 2.1 limiter with CF-Connecting-IP
@dev_only
def test_student_login_limit_uses_cf_connecting_ip(client, proxied):
    def attempt(cf_ip, xff):
        return client.post("/student/auth/login", json={"login": f"nobody{RUN}", "password": "000000"},
                           headers={"CF-Connecting-IP": cf_ip, "X-Forwarded-For": xff})

    # Every request forges another X-Forwarded-For (also its right-most address, as seen on Render):
    # the bucket is still the CF-Connecting-IP address.
    for i in range(10):
        assert attempt(ip(0x100), f"198.51.100.{i}, 10.20.30.{i}").status_code == 401
    r = attempt(ip(0x100), "198.51.100.200, 10.20.30.200")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    # Another client address has its own bucket, even with the same X-Forwarded-For.
    assert attempt(ip(0x101), "198.51.100.200, 10.20.30.200").status_code == 401


@dev_only
def test_cf_header_does_not_matter_without_behind_proxy(client, monkeypatch):
    from routers.student_site import login_limiter

    monkeypatch.setattr(settings, "BEHIND_PROXY", False)
    login_limiter.reset()
    try:
        for i in range(10):  # a new claimed address every time: all share the socket address
            r = client.post("/student/auth/login", json={"login": f"nobody{RUN}", "password": "000000"},
                            headers={"CF-Connecting-IP": ip(0x200 + i)})
            assert r.status_code == 401
        r = client.post("/student/auth/login", json={"login": f"nobody{RUN}", "password": "000000"},
                        headers={"CF-Connecting-IP": ip(0x2ff)})
        assert r.status_code == 429
    finally:
        login_limiter.reset()


# ---------------------------------------------------------------- 2.2 CRM login throttling
@dev_only
def test_five_wrong_passwords_lock_the_pair_only(client, world, proxied):
    email = email_of("victim")
    attacker, owner_ip = ip(0x10), ip(0x11)
    for _ in range(5):
        r = crm_login(client, email, "wrong-" + RUN, attacker)
        assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    # The pair (email, attacker IP) is locked: even the right password is refused, in Russian, with Retry-After.
    r = crm_login(client, email, PASSWORD, attacker)
    assert r.status_code == 429
    assert r.json()["detail"] == "Слишком много попыток входа. Попробуйте через 15 минут."
    assert 0 < int(r.headers["Retry-After"]) <= 15 * 60 + 1
    assert "access_token" not in r.text
    # Refused attempts are not counted and the lock is per pair: the same account from another IP works.
    r = crm_login(client, email, PASSWORD, owner_ip)
    assert r.status_code == 200 and r.json()["access_token"]
    # ... and the attacker's address is still locked after that success elsewhere.
    assert crm_login(client, email, PASSWORD, attacker).status_code == 429
    # Audit: 5 failures, 1 lock (scope email+ip), a success; no password anywhere.
    rows = run(client, _audit_rows, text("details->>'email' = :e").bindparams(e=email))
    actions = [r.action for r in rows]
    assert actions.count(audit.LOGIN_FAILED) == 5 and actions.count(audit.LOGIN_LOCKED) == 1
    locked = next(r for r in rows if r.action == audit.LOGIN_LOCKED)
    assert locked.ip == attacker and locked.details["scope"] == "email+ip" and locked.actor_user_id == world["victim"].id
    assert all(PASSWORD not in json.dumps(r.details) and "wrong-" not in json.dumps(r.details) for r in rows)
    success = run(client, _audit_rows, text("action = 'login_success' AND actor_user_id = :u").bindparams(u=world["victim"].id))
    assert len(success) == 1 and success[0].ip == owner_ip


@dev_only
def test_emergency_unlock_sql_from_the_runbook(client, world, proxied):
    email = email_of("victim")  # locked for ip(0x10) by the previous test
    assert crm_login(client, email, PASSWORD, ip(0x10)).status_code == 429
    runbook = (ROOT / "docs" / "RUNBOOK_EMERGENCY.md").read_text(encoding="utf-8")
    statements = [
        "DELETE FROM auth_login_state WHERE email = lower(trim(:email));",
        "DELETE FROM auth_email_locks WHERE email = lower(trim(:email));",
    ]
    for statement in statements:
        assert statement.replace(":email", "'ВАШ_EMAIL'") in runbook
        run(client, _exec, statement.rstrip(";"), {"email": "  " + email.upper() + " "})
    assert crm_login(client, email, PASSWORD, ip(0x10)).status_code == 200


@dev_only
def test_success_resets_the_pair_counter(client, world, proxied):
    email, address = email_of("spare"), ip(0x20)
    for _ in range(4):
        assert crm_login(client, email, "wrong", address).status_code == 401
    assert crm_login(client, email, PASSWORD, address).status_code == 200
    login_guard.ip_limiter.reset()
    for _ in range(4):
        assert crm_login(client, email, "wrong", address).status_code == 401
    assert crm_login(client, email, PASSWORD, address).status_code == 200  # 4 + 4 wrong, never 5 in a row


@dev_only
def test_unknown_email_behaves_like_an_existing_one(client, world, proxied):
    email, address = f"nobody-{RUN}@example.com", ip(0x30)
    for _ in range(5):
        r = crm_login(client, email, "whatever", address)
        assert r.status_code == 401 and r.json() == {"detail": "Invalid credentials"}
    r = crm_login(client, email, "whatever", address)
    assert r.status_code == 429 and r.json()["detail"].startswith("Слишком много попыток входа. Попробуйте через")
    rows = run(client, _audit_rows, text("details->>'email' = :e").bindparams(e=email))
    assert sorted(r.action for r in rows) == [audit.LOGIN_FAILED] * 5 + [audit.LOGIN_LOCKED]
    assert all(r.actor_user_id is None and r.actor_kind == "system" for r in rows)


@dev_only
def test_ip_limit_ten_attempts_a_minute(client, world, proxied):
    address = ip(0x40)
    for i in range(10):  # ten different emails: the pair lock is not involved
        assert crm_login(client, f"nobody{i}-{RUN}@example.com", "x", address).status_code == 401
    r = crm_login(client, email_of("admin"), PASSWORD, address)
    assert r.status_code == 429 and "Слишком много попыток входа" in r.json()["detail"] and r.headers["Retry-After"]
    # Forged X-Forwarded-For does not help; another client address is not affected.
    assert crm_login(client, email_of("admin"), PASSWORD, address, xff="1.2.3.4").status_code == 429
    assert crm_login(client, email_of("admin"), PASSWORD, ip(0x41)).status_code == 200


@dev_only
def test_email_is_compared_without_case_and_spaces(client, world, proxied):
    email = email_of("admin")
    r = client.post("/auth/login", json={"email": "  " + email.upper().replace("EXAMPLE.COM", "Example.Com") + " ",
                                         "password": PASSWORD}, headers={"CF-Connecting-IP": ip(0x50)})
    assert r.status_code == 200, r.text
    me = client.get("/auth/me", headers=bearer(r.json()["access_token"]))
    assert me.status_code == 200 and me.json()["email"] == email


@dev_only
def test_staff_email_is_stored_in_lower_case(client, world, proxied):
    mixed = f"T4-New-{RUN}@Example.com"
    r = client.post("/staff", headers=admin_headers(world),
                    json={"email": mixed, "full_name": f"t4 new {RUN}", "roles": ["teacher"], "password": PASSWORD + "-long"})
    assert r.status_code == 201, r.text
    world["extra_users"].append(uuid.UUID(r.json()["id"]))
    assert r.json()["email"] == mixed.lower()
    # The same address in another case is a duplicate, and logs in.
    again = client.post("/staff", headers=admin_headers(world),
                        json={"email": mixed.upper(), "full_name": "dup", "roles": ["teacher"]})
    assert again.status_code == 409
    assert crm_login(client, mixed.upper(), PASSWORD + "-long", ip(0x51)).status_code == 200


@dev_only
def test_distributed_guessing_locks_the_email_except_trusted_ips(client, world, proxied):
    email = email_of("owner")
    trusted, fresh = ip(0x60), ip(0x61)
    assert crm_login(client, email, PASSWORD, trusted).status_code == 200  # the owner's device: trusted for 30 days
    # 30 wrong passwords within the window from 6 addresses (5 each: every pair gets locked on the way).
    for n in range(6):
        for _ in range(5):
            assert crm_login(client, email, "guess", ip(0x70 + n)).status_code == 401
    # A new address is refused even with the right password ...
    r = crm_login(client, email, PASSWORD, fresh)
    assert r.status_code == 429 and "Слишком много попыток входа" in r.json()["detail"]
    # ... the trusted address still gets in.
    assert crm_login(client, email, PASSWORD, trusted).status_code == 200
    # Other accounts are not affected.
    assert crm_login(client, email_of("admin"), PASSWORD, fresh).status_code == 200
    locks = run(client, _audit_rows, text("action = 'login_locked' AND details->>'email' = :e").bindparams(e=email))
    assert sum(1 for r in locks if r.details["scope"] == "email") == 1
    # The 30th failure locks its pair and the email at once: it is written as one row with the wider scope.
    assert sum(1 for r in locks if r.details["scope"] == "email+ip") == 5


@dev_only
def test_lock_expires_after_fifteen_minutes(client, world, proxied):
    email, address = email_of("spare"), ip(0x80)
    for _ in range(5):
        assert crm_login(client, email, "wrong", address).status_code == 401
    assert crm_login(client, email, PASSWORD, address).status_code == 429
    # Move the lock 16 minutes into the past instead of waiting.
    run(client, _exec, "UPDATE auth_login_state SET locked_until = locked_until - interval '16 minutes' "
                       "WHERE email = :e AND ip = :ip", {"e": email, "ip": address})
    assert crm_login(client, email, PASSWORD, address).status_code == 200


# ---------------------------------------------------------------- 2.4 refresh tokens
async def _tokens_of(user_id):
    from models.refresh_token import RefreshToken

    return await _rows(RefreshToken, RefreshToken.user_id == user_id)


async def _add_token(user_id, token, with_hash, expires_in_days=30):
    from core.database import AsyncSessionLocal
    from models.refresh_token import RefreshToken

    async with AsyncSessionLocal() as db:
        db.add(RefreshToken(user_id=user_id, token=token, token_hash=refresh_tokens.token_hash(token) if with_hash else None,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days)))
        await db.commit()


async def _drop_tokens(user_id):
    from core.database import AsyncSessionLocal
    from models.refresh_token import RefreshToken

    async with AsyncSessionLocal() as db:
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await db.commit()


async def _set_active(user_id, value):
    from core.database import AsyncSessionLocal
    from models.user import User

    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE users SET is_active = :v WHERE id = :id"), {"v": value, "id": user_id})
        await db.commit()


@dev_only
def test_new_refresh_tokens_are_stored_as_hashes(client, world, proxied):
    user = world["tok"]
    run(client, _drop_tokens, user.id)
    tokens = crm_login(client, user.email, PASSWORD, ip(0x90)).json()
    rows = run(client, _tokens_of, user.id)
    assert len(rows) == 1
    digest = hashlib.sha256(tokens["refresh_token"].encode()).hexdigest()
    assert rows[0].token_hash == digest and rows[0].token == "sha256:" + digest
    # Rotation keeps the rule; the old token stops working, the content of the legacy column is not a token.
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200 and r.json()["refresh_token"] != tokens["refresh_token"]
    rows = run(client, _tokens_of, user.id)
    assert len(rows) == 1 and rows[0].token_hash == hashlib.sha256(r.json()["refresh_token"].encode()).hexdigest()
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": rows[0].token}).status_code == 401
    assert all(tokens["refresh_token"] not in (row.token, row.token_hash) for row in rows)


@dev_only
@pytest.mark.parametrize("with_hash", [False, True], ids=["plain-only (written by the old server)", "plain+hash (filled by 014)"])
def test_legacy_plain_token_still_works_and_moves_to_hash(client, world, with_hash):
    from core.security import create_refresh_token

    user = world["tok"]
    run(client, _drop_tokens, user.id)
    legacy = create_refresh_token(str(user.id))
    run(client, _add_token, user.id, legacy, with_hash)
    r = client.post("/auth/refresh", json={"refresh_token": legacy})
    assert r.status_code == 200, r.text
    new = r.json()["refresh_token"]
    rows = run(client, _tokens_of, user.id)
    # The plain row is gone; the only row left is the hashed one of the new token.
    assert len(rows) == 1 and rows[0].token == "sha256:" + hashlib.sha256(new.encode()).hexdigest()
    assert client.get("/auth/me", headers=bearer(r.json()["access_token"])).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": legacy}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": new}).status_code == 200


@dev_only
def test_refresh_refused_for_inactive_user_and_token_survives(client, world):
    from core.security import create_refresh_token

    user = world["tok"]
    run(client, _drop_tokens, user.id)
    legacy = create_refresh_token(str(user.id))
    run(client, _add_token, user.id, legacy, False)
    run(client, _set_active, user.id, False)
    try:
        r = client.post("/auth/refresh", json={"refresh_token": legacy})
        assert r.status_code == 401 and "access_token" not in r.text
        rows = run(client, _tokens_of, user.id)
        assert len(rows) == 1 and rows[0].token == legacy  # nothing issued, nothing consumed
    finally:
        run(client, _set_active, user.id, True)
    assert client.post("/auth/refresh", json={"refresh_token": legacy}).status_code == 200


@dev_only
def test_logout_revokes_hashed_and_legacy_tokens(client, world, proxied):
    from core.security import create_refresh_token

    user = world["tok"]
    run(client, _drop_tokens, user.id)
    hashed = crm_login(client, user.email, PASSWORD, ip(0x91)).json()["refresh_token"]
    legacy = create_refresh_token(str(user.id))
    run(client, _add_token, user.id, legacy, False)
    assert len(run(client, _tokens_of, user.id)) == 2
    for token in (hashed, legacy):
        assert client.post("/auth/logout", json={"refresh_token": token}).status_code == 200
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401
    assert run(client, _tokens_of, user.id) == []


@dev_only
def test_cleanup_deletes_only_expired_tokens(client, world, monkeypatch):
    from core.security import create_refresh_token

    user = world["tok"]
    run(client, _drop_tokens, user.id)
    live_plain, live_hashed, dead_plain, dead_hashed = (create_refresh_token(str(user.id)) for _ in range(4))
    run(client, _add_token, user.id, live_plain, False, 5)
    run(client, _add_token, user.id, live_hashed, True, 5)
    run(client, _add_token, user.id, dead_plain, False, -1)
    run(client, _add_token, user.id, dead_hashed, True, -1)
    deleted = run(client, refresh_tokens.purge_expired, True)
    assert deleted is not None and deleted >= 2
    left = {row.token for row in run(client, _tokens_of, user.id)}
    assert left == {live_plain, live_hashed}
    # Not more often than once a day: the next call does nothing ...
    run(client, _add_token, user.id, dead_plain, False, -1)
    assert run(client, refresh_tokens.purge_expired) is None
    assert len(run(client, _tokens_of, user.id)) == 3
    # ... until a day has passed; then /auth/refresh triggers it.
    monkeypatch.setattr(refresh_tokens, "_last_purge", time.monotonic() - refresh_tokens.PURGE_INTERVAL_SECONDS - 1)
    assert client.post("/auth/refresh", json={"refresh_token": live_plain}).status_code == 200
    rows = run(client, _tokens_of, user.id)
    assert dead_plain not in {row.token for row in rows} and len(rows) == 2


# ---------------------------------------------------------------- 2.3 audit log
@dev_only
def test_audit_requires_admin(client, world):
    from core.security import create_access_token

    assert client.get("/audit").status_code in (401, 403)
    teacher = bearer(create_access_token(str(world["teacher"].id), "teacher"))
    assert client.get("/audit", headers=teacher).status_code == 403
    r = client.get("/audit", headers=admin_headers(world))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "total", "limit", "offset", "actions"} and body["limit"] == 50
    assert set(audit.ACTIONS) == set(body["actions"])


@dev_only
def test_sensitive_actions_are_written_to_the_audit_log(client, world, proxied):
    admin = admin_headers(world)
    headers = dict(admin, **{"CF-Connecting-IP": ip(0xA0)})
    student_id = world["student"].id
    started = datetime.now(timezone.utc) - timedelta(seconds=5)

    # --- student access: create, view, reset, disable, enable, unlock
    created = client.post(f"/students/{student_id}/access", headers=headers, json={})
    assert created.status_code == 201, created.text
    first_password = created.json()["password"]
    viewed = client.get(f"/students/{student_id}/access/password", headers=headers)
    assert viewed.status_code == 200
    reset = client.post(f"/students/{student_id}/access/reset-password", headers=headers)
    assert reset.status_code == 200
    second_password = reset.json()["password"]
    assert client.patch(f"/students/{student_id}/access", headers=headers, json={"is_active": False}).status_code == 200
    assert client.patch(f"/students/{student_id}/access", headers=headers, json={"is_active": True}).status_code == 200
    # A PATCH that does not change is_active writes nothing.
    assert client.patch(f"/students/{student_id}/access", headers=headers, json={"is_active": True}).status_code == 200
    assert client.post(f"/students/{student_id}/access/unlock", headers=headers).status_code == 200

    # --- staff: create, roles, disable, enable
    staff = client.post("/staff", headers=headers, json={"email": f"t4-staff-{RUN}@example.com", "full_name": f"t4 staff {RUN}",
                                                         "roles": ["teacher"]})
    assert staff.status_code == 201, staff.text
    staff_id, staff_password = staff.json()["id"], staff.json()["password"]
    world["extra_users"].append(uuid.UUID(staff_id))
    assert client.patch(f"/staff/{staff_id}", headers=headers, json={"roles": ["teacher", "methodist"]}).status_code == 200
    assert client.patch(f"/staff/{staff_id}", headers=headers, json={"full_name": f"t4 staff renamed {RUN}"}).status_code == 200
    assert client.patch(f"/staff/{staff_id}", headers=headers, json={"is_active": False}).status_code == 200
    assert client.patch(f"/staff/{staff_id}", headers=headers, json={"is_active": True}).status_code == 200

    # --- deletions: payment, expense, student
    payment = client.post("/finance/payments", headers=headers, json={"student_id": str(student_id), "amount": "1500", "payment_method": "cash"})
    assert payment.status_code == 201, payment.text
    assert client.delete(f"/finance/payments/{payment.json()['id']}", headers=headers).status_code == 200
    assert client.delete(f"/finance/payments/{payment.json()['id']}", headers=headers).status_code == 200  # already cancelled: no second row
    expense = client.post("/finance/expenses", headers=headers, json={"category": "other", "amount": "700", "description": f"t4 expense {RUN}"})
    assert expense.status_code == 201, expense.text
    assert client.delete(f"/finance/expenses/{expense.json()['id']}", headers=headers).status_code == 200
    assert client.delete(f"/students/{student_id}", headers=headers).status_code == 204

    rows = run(client, _audit_rows, text("actor_user_id = :u AND ip = :ip").bindparams(u=world["admin"].id, ip=ip(0xA0)))
    by_action = {}
    for row in rows:
        by_action.setdefault(row.action, []).append(row)
    expected_once = [
        audit.STUDENT_ACCESS_CREATED, audit.STUDENT_PASSWORD_VIEWED, audit.STUDENT_PASSWORD_RESET,
        audit.STUDENT_ACCESS_DISABLED, audit.STUDENT_ACCESS_ENABLED, audit.STUDENT_ACCESS_UNLOCKED,
        audit.STAFF_CREATED, audit.STAFF_ROLES_CHANGED, audit.STAFF_DISABLED, audit.STAFF_ENABLED,
        audit.PAYMENT_DELETED, audit.EXPENSE_DELETED, audit.STUDENT_DELETED,
    ]
    assert {a: len(v) for a, v in by_action.items()} == {a: 1 for a in expected_once}
    for row in rows:
        assert row.actor_kind == "user" and row.created_at >= started
        dumped = json.dumps(row.details, ensure_ascii=False)
        for secret in (first_password, second_password, staff_password):
            assert secret not in dumped
    assert by_action[audit.STUDENT_PASSWORD_VIEWED][0].target_id == str(student_id)
    assert by_action[audit.STUDENT_PASSWORD_VIEWED][0].details["student"] == world["student"].full_name
    roles = by_action[audit.STAFF_ROLES_CHANGED][0].details
    assert roles["before"] == ["teacher"] and roles["after"] == ["teacher", "methodist"]
    assert by_action[audit.STAFF_CREATED][0].target_id == staff_id
    assert by_action[audit.PAYMENT_DELETED][0].details["amount"].startswith("1500")
    assert by_action[audit.EXPENSE_DELETED][0].target_id == expense.json()["id"]

    # --- GET /audit: who, filter by action and date, pages of the requested size
    today = datetime.now(audit_tz()).date().isoformat()
    page = client.get("/audit", headers=admin, params={"action": audit.STAFF_ROLES_CHANGED, "date_from": today, "date_to": today}).json()
    mine = [item for item in page["items"] if item["target_id"] == staff_id]
    assert len(mine) == 1 and mine[0]["actor_name"] == world["admin"].full_name and mine[0]["ip"] == ip(0xA0)
    assert all(item["action"] == audit.STAFF_ROLES_CHANGED for item in page["items"])
    first = client.get("/audit", headers=admin, params={"limit": 2}).json()
    second = client.get("/audit", headers=admin, params={"limit": 2, "offset": 2}).json()
    assert len(first["items"]) == 2 and first["total"] >= len(rows) and first["total"] == second["total"]
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert first["items"][0]["created_at"] >= first["items"][1]["created_at"]  # newest first
    tomorrow = (datetime.now(audit_tz()).date() + timedelta(days=1)).isoformat()
    assert client.get("/audit", headers=admin, params={"date_from": tomorrow}).json()["total"] == 0
    assert client.get("/audit", headers=admin, params={"date_from": tomorrow, "date_to": today}).status_code == 400
    assert client.get("/audit", headers=admin, params={"limit": 500}).status_code == 422


def audit_tz():
    from routers.audit import LOCAL_TZ

    return LOCAL_TZ


@dev_only
def test_failed_audit_write_does_not_break_the_action(client, world, monkeypatch, caplog):
    def broken(_details):
        raise RuntimeError("audit storage is down")

    monkeypatch.setattr(audit, "_clean", broken)
    assert run(client, lambda: audit.record("login_failed", details={"marker": RUN})) is False
    headers = dict(admin_headers(world), **{"CF-Connecting-IP": ip(0xB0)})
    expense = client.post("/finance/expenses", headers=headers, json={"category": "other", "amount": "5", "description": f"t4 expense {RUN}"})
    with caplog.at_level("ERROR", logger="itdeti.audit"):
        r = client.delete(f"/finance/expenses/{expense.json()['id']}", headers=headers)
    assert r.status_code == 200 and r.json()["is_cancelled"] is True       # the action itself is done
    assert any("audit write failed" in rec.message for rec in caplog.records)   # ... and the failure is in the server log
    assert run(client, _audit_rows, text("ip = :ip").bindparams(ip=ip(0xB0))) == []


@dev_only
def test_audit_rows_older_than_180_days_are_deleted_once_a_day(client, world, monkeypatch):
    from models.security import AuditLog

    async def add(days_ago):
        from core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            row = AuditLog(action="login_failed", actor_kind="system", details={"marker": RUN},
                           created_at=datetime.now(timezone.utc) - timedelta(days=days_ago))
            db.add(row)
            await db.commit()
            return row.id

    old_id, kept_id = run(client, add, 181), run(client, add, 179)
    monkeypatch.setattr(audit, "_last_purge", time.monotonic())           # "already cleaned today"
    assert client.get("/audit", headers=admin_headers(world)).status_code == 200
    assert len(run(client, _audit_rows, AuditLog.id.in_([old_id, kept_id]))) == 2
    monkeypatch.setattr(audit, "_last_purge", time.monotonic() - audit.PURGE_INTERVAL_SECONDS - 1)
    assert client.get("/audit", headers=admin_headers(world)).status_code == 200
    left = run(client, _audit_rows, AuditLog.id.in_([old_id, kept_id]))
    assert [row.id for row in left] == [kept_id]


# ---------------------------------------------------------------- prod_ops.py against the dev database
@dev_only
def test_prod_ops_dev_check_preflight_postcheck_counts(tmp_path, capsys):
    assert prod_ops.main(["--dev-check", "preflight"]) == 0
    assert prod_ops.main(["--dev-check", "postcheck", "014"]) == 0
    assert prod_ops.main(["--dev-check", "postcheck", "999"]) == 2
    saved = tmp_path / "counts.json"
    assert prod_ops.main(["--dev-check", "counts", "--save", str(saved)]) == 0
    counts = json.loads(saved.read_text(encoding="utf-8"))
    assert counts["users"] >= 1 and "audit_log" in counts and "refresh_tokens" in counts
    assert prod_ops.main(["--dev-check", "counts", "--compare", str(saved)]) == 0
    saved.write_text(json.dumps(dict(counts, students=counts["students"] + 1)), encoding="utf-8")
    assert prod_ops.main(["--dev-check", "counts", "--compare", str(saved)]) == 1
    out = capsys.readouterr().out
    assert "ИТОГ: норма" in out and "РАСХОЖДЕНИЕ students" in out
    assert "DEV" in out and "yctyhgbhqsbawgjaiock" not in out and "postgresql" not in out


@dev_only
def test_prod_ops_dev_check_backup_and_migrate_gate(tmp_path, capsys):
    backups = tmp_path / "backups"
    # No backup yet -> the migration is refused before anything is sent to the database.
    assert prod_ops.main(["--dev-check", "--backup-dir", str(backups), "migrate", "014_security.sql"]) == 2
    assert prod_ops.main(["--dev-check", "migrate", "014_security.sql"]) == 2          # no --backup-dir in dev mode
    assert prod_ops.main(["--dev-check", "--backup-dir", str(backups), "migrate", "013_access_model.sql"]) == 2
    assert prod_ops.main(["--dev-check", "--backup-dir", str(backups), "backup"]) == 0
    files = list(backups.glob("itdeti_backup_*.json"))
    assert len(files) == 1 and not list(backups.glob("*.tmp"))
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["verified"] is True and len(payload["tables"]) >= 25
    for name, info in payload["tables"].items():
        assert info["rows"] == len(info["data"]), name
    assert payload["tables"]["users"]["rows"] >= 1 and "email" in payload["tables"]["users"]["data"][0]
    out = capsys.readouterr().out
    assert "users:" in out and "@" not in out          # names and numbers only, no data
    # With a fresh verified backup of the same database the gate opens (014 is idempotent on dev).
    assert prod_ops.main(["--dev-check", "--backup-dir", str(backups), "migrate", "014_security.sql", "--check-only"]) == 0
    assert prod_ops.main(["--dev-check", "--backup-dir", str(backups), "migrate", "014_security.sql"]) == 0
    assert prod_ops.main(["--dev-check", "postcheck", "014"]) == 0
    # A backup older than 30 minutes no longer counts.
    old = time.time() - 31 * 60
    os.utime(files[0], (old, old))
    assert prod_ops.main(["--dev-check", "--backup-dir", str(backups), "migrate", "014_security.sql", "--check-only"]) == 2
