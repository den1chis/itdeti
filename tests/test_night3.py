"""Night 3 (preparing for production): self-deactivation guard, /auth/refresh and is_active, docs switched off
by default, explicit CORS list, client IP behind a proxy.

The unit tests need no database. The integration tests use the DEV database (.env), create their own uniquely
named users and delete them at the end, like the other integration tests."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from starlette.requests import Request

from core.client_ip import client_ip
from core.config import Settings, settings
from scripts.devguard import is_dev_database

ROOT = Path(__file__).resolve().parent.parent
RUN = uuid.uuid4().hex[:6]


def _request(socket_ip="10.0.0.1", xff=None):
    headers = [] if xff is None else [(b"x-forwarded-for", v.encode()) for v in ([xff] if isinstance(xff, str) else xff)]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers, "client": (socket_ip, 1234)})


# ---------------------------------------------------------------- 1.5 client IP behind a proxy (unit)
def test_client_ip_ignores_header_when_not_behind_proxy():
    assert client_ip(_request("10.0.0.1", "1.2.3.4"), behind_proxy=False) == "10.0.0.1"
    assert client_ip(_request("10.0.0.1"), behind_proxy=False) == "10.0.0.1"


def test_client_ip_takes_rightmost_address_behind_proxy():
    # The client forges the first address; the proxy appends the real one on the right.
    assert client_ip(_request("10.0.0.1", "6.6.6.6, 203.0.113.7"), behind_proxy=True, trusted_hops=1) == "203.0.113.7"
    assert client_ip(_request("10.0.0.1", "7.7.7.7, 8.8.8.8, 203.0.113.7"), behind_proxy=True, trusted_hops=1) == "203.0.113.7"
    # Two header lines are one list.
    assert client_ip(_request("10.0.0.1", ["6.6.6.6", "203.0.113.7"]), behind_proxy=True, trusted_hops=1) == "203.0.113.7"
    # Two trusted proxies: the second address from the right.
    assert client_ip(_request("10.0.0.1", "6.6.6.6, 203.0.113.7, 10.1.1.1"), behind_proxy=True, trusted_hops=2) == "203.0.113.7"
    assert client_ip(_request("10.0.0.1", "2001:db8::1"), behind_proxy=True, trusted_hops=1) == "2001:db8::1"


def test_client_ip_falls_back_to_socket_address():
    assert client_ip(_request("10.0.0.1"), behind_proxy=True, trusted_hops=1) == "10.0.0.1"           # no header
    assert client_ip(_request("10.0.0.1", "203.0.113.7"), behind_proxy=True, trusted_hops=2) == "10.0.0.1"  # too short
    assert client_ip(_request("10.0.0.1", "1.1.1.1, not-an-ip"), behind_proxy=True, trusted_hops=1) == "10.0.0.1"
    assert client_ip(_request("10.0.0.1", " , "), behind_proxy=True, trusted_hops=1) == "10.0.0.1"
    assert client_ip(_request("10.0.0.1", "203.0.113.7"), behind_proxy=True, trusted_hops=0) == "203.0.113.7"  # 0 -> 1


def test_client_ip_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "BEHIND_PROXY", False)
    assert client_ip(_request("10.0.0.1", "1.2.3.4")) == "10.0.0.1"
    monkeypatch.setattr(settings, "BEHIND_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    assert client_ip(_request("10.0.0.1", "6.6.6.6, 1.2.3.4")) == "1.2.3.4"


# ---------------------------------------------------------------- 1.3 / 1.4 / 1.5 defaults
def test_defaults_are_safe(monkeypatch):
    for name in ("ENABLE_DOCS", "BEHIND_PROXY", "TRUSTED_PROXY_HOPS", "CORS_ORIGINS", "CORS_EXTRA_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://x@localhost/x", JWT_SECRET_KEY="x")
    assert s.ENABLE_DOCS is False and s.BEHIND_PROXY is False and s.TRUSTED_PROXY_HOPS == 1
    import main

    assert main.cors_origins(s.CORS_ORIGINS, s.CORS_EXTRA_ORIGINS) == [
        "https://den1chis.github.io", "http://localhost:5500", "http://127.0.0.1:5500",
        "http://localhost:5501", "http://127.0.0.1:5501",
    ]


def test_empty_values_mean_defaults(monkeypatch):
    # .env.example lists the names without values: "ENABLE_DOCS=" must not fail and must not switch anything on.
    for name in ("ENABLE_DOCS", "BEHIND_PROXY", "TRUSTED_PROXY_HOPS", "CORS_ORIGINS"):
        monkeypatch.setenv(name, "")
    s = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://x@localhost/x", JWT_SECRET_KEY="x")
    assert s.ENABLE_DOCS is False and s.BEHIND_PROXY is False and s.TRUSTED_PROXY_HOPS == 1
    assert s.CORS_ORIGINS.startswith("https://den1chis.github.io")


def test_cors_origins_parsing():
    import main

    assert main.cors_origins("https://a.example, *,https://b.example/ ", "https://a.example,https://c.example") == [
        "https://a.example", "https://b.example", "https://c.example",
    ]
    assert "*" not in main.CORS_ORIGINS


# ---------------------------------------------------------------- 1.3 docs off unless ENABLE_DOCS=true
_DOCS_PROBE = r"""
import sys, dotenv
dotenv.load_dotenv = lambda *a, **k: False   # do not read itdeti/.env: test the bare environment
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
import main
c = TestClient(main.app)
print(" ".join(str(c.get(p).status_code) for p in ("/docs", "/redoc", "/openapi.json", "/health")))
"""


def _docs_statuses(tmp_path, enable_docs):
    env = {k: v for k, v in os.environ.items() if k not in ("ENABLE_DOCS", "APP_ENV")}
    env.update({"DATABASE_URL": "postgresql+asyncpg://nobody:x@localhost:1/none", "JWT_SECRET_KEY": "probe",
                "PYTHONDONTWRITEBYTECODE": "1", "DISABLE_SCHEDULER": "true"})
    if enable_docs is not None:
        env["ENABLE_DOCS"] = enable_docs
    out = subprocess.run([sys.executable, "-c", _DOCS_PROBE, str(ROOT)], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1].split()


def test_docs_are_off_without_variable(tmp_path):
    assert _docs_statuses(tmp_path, None) == ["404", "404", "404", "200"]
    assert _docs_statuses(tmp_path, "false") == ["404", "404", "404", "200"]


def test_docs_are_on_with_enable_docs_true(tmp_path):
    assert _docs_statuses(tmp_path, "true") == ["200", "200", "200", "200"]


# ---------------------------------------------------------------- 1.4 CORS preflight
@pytest.fixture(scope="module")
def plain_client():
    import main

    return TestClient(main.app)  # no lifespan: preflight needs no database


def _preflight(c, origin):
    return c.options("/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "authorization,content-type"})


@pytest.mark.parametrize("origin", ["https://den1chis.github.io", "http://localhost:5500", "http://127.0.0.1:5501"])
def test_cors_preflight_allowed_origin(plain_client, origin):
    r = _preflight(plain_client, origin)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize("origin", ["https://evil.example", "https://den1chis.github.io.evil.example", "null"])
def test_cors_preflight_foreign_origin(plain_client, origin):
    r = _preflight(plain_client, origin)
    assert "access-control-allow-origin" not in r.headers
    # A simple request from a foreign origin is answered, but without CORS headers the browser hides it.
    r = plain_client.get("/health", headers={"Origin": origin})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


def test_cors_simple_request_allowed_origin(plain_client):
    r = plain_client.get("/health", headers={"Origin": "https://den1chis.github.io"})
    assert r.headers["access-control-allow-origin"] == "https://den1chis.github.io"


# ---------------------------------------------------------------- integration (dev database)
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


async def _create_users():
    from core.database import AsyncSessionLocal
    from core.security import hash_password
    from models.user import User

    users = {}
    async with AsyncSessionLocal() as db:
        for key, roles in {"admin1": ["admin"], "admin2": ["admin"], "teacher": ["teacher"]}.items():
            users[key] = User(email=f"t3-{key}-{RUN}@example.com", full_name=f"t3 {key} {RUN}", role=roles[0],
                              roles=roles, is_active=True, password_hash=hash_password("pw-" + RUN))
            db.add(users[key])
        await db.commit()
    return users


async def _delete_users(users):
    from core.database import AsyncSessionLocal
    from models.refresh_token import RefreshToken
    from models.user import User

    ids = [u.id for u in users.values()]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(ids)))
        await db.execute(delete(User).where(User.id.in_(ids)))
        await db.commit()


async def _set_active(user_id, value):
    from core.database import AsyncSessionLocal
    from models.user import User

    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == user_id).values(is_active=value))
        await db.commit()


async def _refresh_count(user_id):
    from core.database import AsyncSessionLocal
    from models.refresh_token import RefreshToken

    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.user_id == user_id))


@pytest.fixture(scope="module")
def users(client):
    if not is_dev_database(settings.DATABASE_URL):  # pragma: no cover
        pytest.skip("integration tests run only against the dev database")
    u = run(client, _create_users)
    yield u
    run(client, _delete_users, u)


def _login(client, user):
    r = client.post("/auth/login", json={"email": user.email, "password": "pw-" + RUN})
    assert r.status_code == 200, r.text
    return r.json()


@dev_only
def test_admin_cannot_deactivate_or_demote_self(client, users):
    a1 = bearer(_login(client, users["admin1"])["access_token"])
    me = users["admin1"].id
    r = client.patch(f"/staff/{me}", headers=a1, json={"is_active": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "self_deactivation"
    for roles in (["teacher"], ["methodist"], ["teacher", "methodist"]):
        r = client.patch(f"/staff/{me}", headers=a1, json={"roles": roles})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "self_demotion"
    # Nothing changed; the token still works.
    r = client.get("/staff", headers=a1)
    assert r.status_code == 200
    mine = next(s for s in r.json() if s["id"] == str(me))
    assert mine["is_active"] is True and mine["roles"] == ["admin"]
    # Everything else on oneself stays allowed: name, phone, adding a role, is_active=true.
    r = client.patch(f"/staff/{me}", headers=a1, json={"full_name": f"t3 admin1 renamed {RUN}", "is_active": True})
    assert r.status_code == 200 and r.json()["full_name"] == f"t3 admin1 renamed {RUN}"
    r = client.patch(f"/staff/{me}", headers=a1, json={"roles": ["admin", "teacher"]})
    assert r.status_code == 200 and r.json()["roles"] == ["admin", "teacher"]
    assert client.patch(f"/staff/{me}", headers=a1, json={"roles": ["admin"]}).status_code == 200


@dev_only
def test_another_admin_can_still_deactivate_and_demote(client, users):
    a1 = bearer(_login(client, users["admin1"])["access_token"])
    other = users["admin2"].id
    r = client.patch(f"/staff/{other}", headers=a1, json={"roles": ["teacher"]})
    assert r.status_code == 200 and r.json()["roles"] == ["teacher"]
    r = client.patch(f"/staff/{other}", headers=a1, json={"roles": ["admin"], "is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.patch(f"/staff/{other}", headers=a1, json={"is_active": True}).status_code == 200


@dev_only
def test_refresh_works_for_active_user(client, users):
    tokens = _login(client, users["teacher"])
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200 and r.json()["access_token"] and r.json()["refresh_token"] != tokens["refresh_token"]


@dev_only
def test_refresh_refused_for_inactive_user(client, users):
    user = users["teacher"]
    tokens = _login(client, user)
    before = run(client, _refresh_count, user.id)
    # Switched off directly in the database (not through /staff, so the refresh tokens are still there).
    run(client, _set_active, user.id, False)
    try:
        r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 401
        assert "access_token" not in r.text
        assert run(client, _refresh_count, user.id) == before  # no new token was issued
        run(client, _set_active, user.id, None)                # NULL counts as inactive as well
        assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    finally:
        run(client, _set_active, user.id, True)
    # Active again -> the same refresh token works (it was not consumed by the refused attempts).
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 200


@dev_only
def test_refresh_refused_for_deleted_user(client):
    from core.security import create_refresh_token

    r = client.post("/auth/refresh", json={"refresh_token": create_refresh_token(str(uuid.uuid4()))})
    assert r.status_code == 401


# ---------------------------------------------------------------- 1.5 rate limiter buckets (dev database)
def _student_login(client, xff):
    return client.post("/student/auth/login", json={"login": f"nobody{RUN}", "password": "000000"},
                       headers={"X-Forwarded-For": xff})


@dev_only
def test_forged_first_address_does_not_change_bucket(client, monkeypatch):
    from routers.student_site import login_limiter

    monkeypatch.setattr(settings, "BEHIND_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    login_limiter.reset()
    try:
        for i in range(10):  # a new forged "client address" every time, the proxy-appended one is the same
            assert _student_login(client, f"198.51.100.{i}, 203.0.113.9").status_code == 401
        r = _student_login(client, "198.51.100.200, 203.0.113.9")
        assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
        # Another real client (another right-most address) has its own bucket.
        assert _student_login(client, "203.0.113.9, 203.0.113.10").status_code == 401
    finally:
        login_limiter.reset()


@dev_only
def test_header_ignored_without_behind_proxy(client, monkeypatch):
    from routers.student_site import login_limiter

    monkeypatch.setattr(settings, "BEHIND_PROXY", False)
    login_limiter.reset()
    try:
        for i in range(10):  # every request claims another address; all of them share the socket address
            assert _student_login(client, f"203.0.113.{i}").status_code == 401
        assert _student_login(client, "203.0.113.250").status_code == 429
    finally:
        login_limiter.reset()


def test_app_env_defaults_to_production(monkeypatch):
    # Part 2: a missing APP_ENV must never mean "development" (that value runs create_all on start).
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://x@localhost/x", JWT_SECRET_KEY="x")
    assert s.APP_ENV == "production"
