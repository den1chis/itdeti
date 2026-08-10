from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from core.database import engine, Base
from core.config import settings

# Импорт моделей — нужен чтобы Base знал о них
import models.user
import models.refresh_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    async with engine.begin() as conn:
        # Только для dev — в prod используем миграции Alembic
        if settings.APP_ENV == "development":
            await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(
    title="ITdeti.kz AI System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В prod заменить на конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роутеры
from routers import auth
app.include_router(auth.router)


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.APP_ENV}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_ENV == "development",
    )
