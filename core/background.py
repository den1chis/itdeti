"""Single entry point for background (timer) jobs.

At the moment the API has no background jobs: reminders and notification processing are triggered
by HTTP requests. Any future job (reminders, timed notification processing, ...) must be registered
here, so that DISABLE_SCHEDULER=true in .env switches all of them off at once."""
import asyncio
import logging
from typing import Awaitable, Callable, List

from core.config import settings

log = logging.getLogger("itdeti")

JobFactory = Callable[[], Awaitable[None]]
_JOBS: List[JobFactory] = []
_running: List[asyncio.Task] = []


def register_job(factory: JobFactory) -> None:
    _JOBS.append(factory)


def start_background_jobs() -> None:
    if settings.DISABLE_SCHEDULER:
        log.warning("DISABLE_SCHEDULER=true: background jobs are not started")
        return
    for factory in _JOBS:
        _running.append(asyncio.create_task(factory()))


async def stop_background_jobs() -> None:
    for task in _running:
        task.cancel()
    for task in _running:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _running.clear()
