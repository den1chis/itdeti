from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ANTHROPIC_API_KEY: Optional[str] = None
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    # true -> background jobs (reminders, timed notification processing, ...) are not started.
    # Useful for local work against the dev database.
    DISABLE_SCHEDULER: bool = False
    # Student site (itdetiLearn): short-lived access token, no refresh token.
    STUDENT_TOKEN_EXPIRE_MINUTES: int = 120
    # CORS: explicit list of allowed origins (comma separated). One "https://den1chis.github.io" covers both
    # GitHub Pages sites (CRM and the student site): an origin has no path. Auth goes in the Authorization
    # header, not in cookies.
    CORS_ORIGINS: str = (
        "https://den1chis.github.io,"
        "http://localhost:5500,http://127.0.0.1:5500,"
        "http://localhost:5501,http://127.0.0.1:5501"
    )
    # Extra CORS origins (comma separated) added to CORS_ORIGINS.
    CORS_EXTRA_ORIGINS: str = ""
    # Swagger / ReDoc / openapi.json are served only when this is explicitly true (not tied to APP_ENV).
    ENABLE_DOCS: bool = False
    # Client IP behind a reverse proxy (Render): true -> take the address the trusted proxy appended to
    # X-Forwarded-For (core/client_ip.py); false -> the socket address, the header is ignored.
    BEHIND_PROXY: bool = False
    # How many trusted proxies append to X-Forwarded-For (the client address is the N-th from the right).
    TRUSTED_PROXY_HOPS: int = 1
    # Fernet key for viewable student passwords (core/student_secrets.py). Empty -> viewing is switched off.
    STUDENT_PASSWORD_KEY: Optional[str] = None

    @field_validator("CORS_ORIGINS", "ENABLE_DOCS", "BEHIND_PROXY", "TRUSTED_PROXY_HOPS", mode="before")
    @classmethod
    def _empty_means_default(cls, value, info):
        # "NAME=" (empty, as in .env.example) means "not set": use the safe default instead of failing.
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
