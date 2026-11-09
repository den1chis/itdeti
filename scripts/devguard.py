"""Protection against running dev-only scripts (seed, migrations) on the production database.

A script may run only when DATABASE_URL points to a known dev project or to a local database.
The list is hard-coded on purpose: editing .env alone must not be enough to point a seed at prod."""
import sys
from urllib.parse import urlparse

# Supabase project refs of DEV projects. itdeti-dev:
DEV_PROJECT_REFS = {"yctyhgbhqsbawgjaiock"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_dev_database(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.replace("+asyncpg", ""))
    host = (parsed.hostname or "").lower()
    user = parsed.username or ""
    if host in LOCAL_HOSTS:
        return True
    # Session pooler: user is "postgres.<project-ref>"; direct connection: host is "db.<project-ref>.supabase.co".
    refs = set()
    if "." in user:
        refs.add(user.split(".", 1)[1])
    if host.startswith("db.") and host.endswith(".supabase.co"):
        refs.add(host.split(".")[1])
    return bool(refs & DEV_PROJECT_REFS)


def require_dev_database(url: str, app_env: str = "") -> None:
    if app_env.lower() in {"production", "prod"}:
        sys.exit("REFUSED: APP_ENV is production. This script runs only against the dev database.")
    if not is_dev_database(url):
        sys.exit(
            "REFUSED: DATABASE_URL does not point to the dev project (itdeti-dev) or localhost. "
            "This script must never run against production."
        )
