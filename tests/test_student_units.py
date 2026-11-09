"""Pure unit tests (no database): login/password helpers, rate limiter, dev-database guard, student tokens."""
import re
import uuid

from core.rate_limit import SlidingWindowLimiter
from core.security import create_access_token
from core.student_auth import create_student_token, decode_student_token
from core.student_login import generate_password, is_valid_login, login_base, login_candidate, transliterate
from scripts.devguard import is_dev_database


def test_transliteration_and_login_shape():
    assert transliterate("Егор") == "egor"
    assert transliterate("Әлия") == "aliya"
    assert login_base("Щукин Артём") == "shchukin"
    assert login_base("   ") == "student"
    assert login_base("123") == "student"
    for digits in (2, 3):
        login = login_candidate("Тимур Ахметов", digits)
        assert re.fullmatch(r"timur\d{%d}" % digits, login)
        assert is_valid_login(login)
    assert not is_valid_login("ab")
    assert not is_valid_login("Egor12")  # logins are stored lower-case
    assert not is_valid_login("егор12")


def test_password_is_six_random_digits():
    passwords = {generate_password() for _ in range(50)}
    assert all(re.fullmatch(r"\d{6}", p) for p in passwords)
    assert len(passwords) > 40


def test_rate_limiter_blocks_after_limit():
    limiter = SlidingWindowLimiter(max_calls=3, period_seconds=60)
    assert [limiter.hit("1.2.3.4") for _ in range(3)] == [None, None, None]
    assert limiter.hit("1.2.3.4") > 0
    assert limiter.hit("5.6.7.8") is None  # other IPs are independent
    limiter.reset()
    assert limiter.hit("1.2.3.4") is None


def test_dev_guard():
    assert is_dev_database("postgresql+asyncpg://postgres.yctyhgbhqsbawgjaiock:x@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres")
    assert is_dev_database("postgresql://u:p@localhost:5432/db")
    assert not is_dev_database("postgresql+asyncpg://postgres.someprodref:x@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres")
    assert not is_dev_database("postgresql://postgres:x@db.someprodref.supabase.co:5432/postgres")
    assert not is_dev_database("")


def test_student_token_contains_only_role_and_student_id():
    from jose import jwt

    from core.config import settings

    sid = uuid.uuid4()
    token = create_student_token(sid)
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert set(payload) == {"role", "student_id", "exp"}
    assert payload["role"] == "student" and payload["student_id"] == str(sid)
    assert decode_student_token(token) == sid
    # A CRM token is never accepted as a student token.
    assert decode_student_token(create_access_token(str(uuid.uuid4()), "student")) is None
    assert decode_student_token("garbage") is None
