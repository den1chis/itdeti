"""Viewable student passwords (night 2): the password is stored twice — bcrypt hash for login
(student_accounts.password_hash) and Fernet-encrypted for viewing by the teacher/admin
(student_accounts.password_enc). The key lives only on the server: env STUDENT_PASSWORD_KEY
(generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").

Without the key everything works except viewing: password_enc is left empty and the view route answers
503 password_view_disabled. Staff passwords are never stored this way."""
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings


class PasswordViewDisabled(Exception):
    """STUDENT_PASSWORD_KEY is not set (or is not a valid Fernet key)."""


class PasswordNotReadable(Exception):
    """password_enc is empty or was encrypted with another key."""


def _fernet() -> Optional[Fernet]:
    key = (settings.STUDENT_PASSWORD_KEY or "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        return None


def view_enabled() -> bool:
    return _fernet() is not None


def encrypt_password(password: str) -> Optional[str]:
    """Encrypted password for student_accounts.password_enc, or None when viewing is switched off."""
    fernet = _fernet()
    return fernet.encrypt(password.encode()).decode() if fernet else None


def decrypt_password(token: Optional[str]) -> str:
    fernet = _fernet()
    if fernet is None:
        raise PasswordViewDisabled()
    if not token:
        raise PasswordNotReadable()
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise PasswordNotReadable() from exc
