"""Release marker shown by GET /health, so a deploy script can tell the new build from the old one.

APP_VERSION is a constant changed by hand with every release (night). "commit" comes from the environment
variable Render sets for Git-backed services (RENDER_GIT_COMMIT); empty when it is not there (local work)."""
import os

APP_VERSION = "2026-11-12-night4"


def git_commit() -> str:
    return (os.environ.get("RENDER_GIT_COMMIT") or "")[:7]
