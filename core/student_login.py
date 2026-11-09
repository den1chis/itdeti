"""Pure helpers for student credentials (no DB/framework imports, easy to test).

Login = first name in Latin letters + 2-3 digits (e.g. "egor12"); password = 6 digits from `secrets`."""
import re
import secrets
from typing import Optional

LOGIN_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PASSWORD_LENGTH = 6
BASE_MAX_LEN = 12

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Kazakh letters
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}


def transliterate(text: str) -> str:
    out = []
    for ch in (text or "").lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif "a" <= ch <= "z":
            out.append(ch)
    return "".join(out)


def login_base(full_name: str) -> str:
    """First word of the name in Latin letters, at most 12 characters; 'student' if nothing is left."""
    words = (full_name or "").strip().split()
    base = transliterate(words[0]) if words else ""
    return (base or "student")[:BASE_MAX_LEN]


def login_candidate(full_name: str, digits: int) -> str:
    low, high = 10 ** (digits - 1), 10 ** digits
    return f"{login_base(full_name)}{low + secrets.randbelow(high - low)}"


def normalize_login(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def is_valid_login(value: str) -> bool:
    return bool(LOGIN_RE.fullmatch(value or ""))


def generate_password() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PASSWORD_LENGTH))
