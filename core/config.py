from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
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
    # Extra CORS origins (comma separated) on top of the built-in local student-site addresses.
    CORS_EXTRA_ORIGINS: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
