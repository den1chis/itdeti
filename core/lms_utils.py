"""Pure helpers for the LMS (no database or framework imports, so they are trivially testable)."""
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

_ID = r"[A-Za-z0-9_-]{10,}"


def get_embed_url(url: Optional[str]) -> Optional[str]:
    """Convert a Google Drive / Docs share link to an embeddable (iframe) URL.

    Returns None when the link is not a recognised Google link; the UI then shows
    only an "open in new tab" button. The original URL is always kept untouched in the database.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme != "https":
        return None

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host == "drive.google.com":
        match = re.match(rf"^(?:/u/\d+)?/file/d/({_ID})(?:/|$)", path)
        if match:
            return f"https://drive.google.com/file/d/{match.group(1)}/preview"
        match = re.match(rf"^(?:/u/\d+)?/drive/folders/({_ID})(?:/|$)", path)
        if match:
            return f"https://drive.google.com/embeddedfolderview?id={match.group(1)}#list"
        if path in ("/open", "/uc"):
            file_id = (parse_qs(parsed.query).get("id") or [""])[0]
            if re.fullmatch(_ID, file_id):
                return f"https://drive.google.com/file/d/{file_id}/preview"
        return None

    if host == "docs.google.com":
        match = re.match(rf"^(?:/u/\d+)?/(document|presentation|spreadsheets)/d/({_ID})(?:/|$)", path)
        if match:
            return f"https://docs.google.com/{match.group(1)}/d/{match.group(2)}/preview"
        return None

    return None


def is_http_url(url: str) -> bool:
    """Only plain http(s) links are accepted as materials (blocks javascript:, data:, file: and so on)."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
