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


app = FastAPI(
    title="itdeti AI System",
    version="2.0.0",
    lifespan=lifespan,
)

# Local addresses of the student site (itdetiLearn, `python -m http.server 5500`).
# NB: "*" is still in the list (existing behaviour, CRM depends on it); the explicit entries keep the
# student site working once the wildcard is replaced by a real list of origins.
STUDENT_SITE_LOCAL_ORIGINS = ["http://localhost:5500", "http://127.0.0.1:5500"]
CORS_ORIGINS = ["*"] + STUDENT_SITE_LOCAL_ORIGINS + [
    origin.strip() for origin in settings.CORS_EXTRA_ORIGINS.split(",") if origin.strip()
]

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
