import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.background import start_background_jobs, stop_background_jobs
from core.config import settings
from core.database import Base, engine

import models.expense
import models.lesson
import models.notification
import models.parent
import models.payment
import models.refresh_token
import models.recurring_event
import models.recurring_expense
import models.recurring_income
import models.schedule
import models.student
import models.user

from routers import auth, events, finance, lessons, manual_lessons, notifications, recurring_events, students, upcoming


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.APP_ENV == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    start_background_jobs()
    yield
    await stop_background_jobs()
    await engine.dispose()


def docs_urls(enabled: bool) -> dict:
    """Swagger UI, ReDoc and openapi.json exist only when ENABLE_DOCS=true (explicitly, not via APP_ENV)."""
    if enabled:
        return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def cors_origins(primary: str, extra: str = "") -> list:
    """Explicit list of allowed origins: CORS_ORIGINS + CORS_EXTRA_ORIGINS (comma separated, no "*")."""
    origins = []
    for origin in f"{primary},{extra}".split(","):
        origin = origin.strip().rstrip("/")
        if origin and origin != "*" and origin not in origins:
            origins.append(origin)
    return origins


app = FastAPI(
    title="itdeti AI System",
    version="2.0.0",
    lifespan=lifespan,
    **docs_urls(settings.ENABLE_DOCS),
)

# Explicit origins instead of "*": CRM and the student site on GitHub Pages (https://den1chis.github.io),
# local copies on ports 5500/5501, plus CORS_EXTRA_ORIGINS. Auth is the Authorization header (no cookies).
CORS_ORIGINS = cors_origins(settings.CORS_ORIGINS, settings.CORS_EXTRA_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(students.router)
app.include_router(lessons.router)
app.include_router(manual_lessons.router)
app.include_router(finance.router)
app.include_router(events.router)
app.include_router(recurring_events.router)
app.include_router(notifications.router)
app.include_router(upcoming.router)

# LMS (courses/lessons/materials). Additive and isolated: if anything in it fails to import,
# the rest of the API keeps working and the error is logged.
try:
    import models.lms  # noqa: F401
    from routers import lms

    app.include_router(lms.router)
except Exception:  # pragma: no cover
    logging.getLogger("itdeti").exception("LMS module failed to load; the rest of the API is unaffected")

# Tasks / to-do (migration 011). Isolated the same way as LMS.
try:
    import models.tasks  # noqa: F401
    from routers import tasks as tasks_router

    app.include_router(tasks_router.router)
except Exception:  # pragma: no cover
    logging.getLogger("itdeti").exception("Tasks module failed to load; the rest of the API is unaffected")

# Student site (migration migrations/001): student login + cabinet (/student/...) and the admin
# "student access" block (/students/{id}/access). Isolated the same way as LMS.
try:
    import models.lms  # noqa: F401,F811
    import models.student_account  # noqa: F401
    from routers import student_access, student_site

    app.include_router(student_site.router)
    app.include_router(student_access.router)
except Exception:  # pragma: no cover
    logging.getLogger("itdeti").exception("Student site module failed to load; the rest of the API is unaffected")

# Access model (night 2, migrations 012-013): teacher read-only routes (/teacher/*) and staff management
# (/staff, teacher assignment). Isolated the same way: if they fail to load, the rest of the API works.
try:
    from routers import staff as staff_router
    from routers import teacher as teacher_router

    app.include_router(teacher_router.router)
    app.include_router(staff_router.router)
except Exception:  # pragma: no cover
    logging.getLogger("itdeti").exception("Teacher/staff module failed to load; the rest of the API is unaffected")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.APP_ENV}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
    )
