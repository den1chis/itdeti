from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import Base, engine

import models.expense
import models.lesson
import models.notification
import models.parent
import models.payment
import models.refresh_token
import models.schedule
import models.student
import models.user

from routers import auth, events, finance, lessons, notifications, students, upcoming


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.APP_ENV == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="itdeti AI System",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(students.router)
app.include_router(lessons.router)
app.include_router(finance.router)
app.include_router(events.router)
app.include_router(notifications.router)
app.include_router(upcoming.router)

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
