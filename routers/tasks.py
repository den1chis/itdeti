"""Tasks / to-do API (teacher/admin only). Everything lives under /tasks.

One-off tasks: optional date ("без срока" when null), done flag.
Recurring tasks: a series (daily/weekly/monthly/yearly) whose occurrences are expanded on read;
per-occurrence done / move / skip are stored as overrides (see core/task_recurrence.py)."""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import teacher_or_admin
from core.access import require_admin
from core.task_recurrence import Override, build_feed, is_valid_occurrence, next_occurrence, single_item
from models.tasks import Task, TaskOccurrence, TaskProject
from schemas.tasks import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    TaskCreate,
    TaskDone,
    TaskFeed,
    TaskItem,
    TaskMove,
    TaskSkip,
    TaskUpdate,
)

# Night 2 access model: the whole router is admin only (core.access).
router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_admin), Depends(teacher_or_admin)])
LOCAL_TZ = ZoneInfo("Asia/Almaty")
MAX_RANGE_DAYS = 120
KEEP_DONE_HOURS = 36  # the UI hides done tasks after the local day ends; the server just keeps a margin
RULE_FIELDS = ("due_date", "repeat", "repeat_interval", "repeat_weekdays", "repeat_until")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _task_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "notes": task.notes,
        "project_id": task.project_id,
        "due_date": task.due_date,
        "is_done": bool(task.is_done),
        "done_at": task.done_at,
        "repeat": task.repeat or "none",
        "repeat_interval": task.repeat_interval or 1,
        "repeat_weekdays": list(task.repeat_weekdays or []),
        "repeat_until": task.repeat_until,
        "source": task.source or "manual",
        "sort_order": task.sort_order or 0,
        "created_at": task.created_at,
    }


def _override(row: TaskOccurrence) -> Override:
    return Override(occurrence_date=row.occurrence_date, done_at=row.done_at,
                    moved_to=row.moved_to, is_skipped=bool(row.is_skipped))


async def _overrides_for(db: AsyncSession, task_ids: List[UUID]) -> Dict[UUID, List[Override]]:
    result: Dict[UUID, List[Override]] = {}
    if not task_ids:
        return result
    rows = (await db.execute(select(TaskOccurrence).where(TaskOccurrence.task_id.in_(task_ids)))).scalars().all()
    for row in rows:
        result.setdefault(row.task_id, []).append(_override(row))
    return result


async def _get_task(db: AsyncSession, task_id: UUID) -> Task:
    task = await db.scalar(select(Task).where(Task.id == task_id))
    if not task:
        raise HTTPException(404, "Задача не найдена")
    return task


async def _check_project(db: AsyncSession, project_id: Optional[UUID]) -> None:
    if project_id is not None and not await db.scalar(select(TaskProject.id).where(TaskProject.id == project_id)):
        raise HTTPException(400, "Проект не найден")


async def _get_override(db: AsyncSession, task_id: UUID, occurrence_date: date) -> TaskOccurrence:
    row = await db.scalar(select(TaskOccurrence).where(
        TaskOccurrence.task_id == task_id, TaskOccurrence.occurrence_date == occurrence_date))
    if not row:
        row = TaskOccurrence(task_id=task_id, occurrence_date=occurrence_date, is_skipped=False)
        db.add(row)
    return row


def _require_occurrence(task: Task, occurrence_date: Optional[date]) -> date:
    if occurrence_date is None:
        raise HTTPException(400, "Для повторяющейся задачи нужна дата повтора (occurrence_date)")
    if not is_valid_occurrence(_task_dict(task), occurrence_date):
        raise HTTPException(400, "В эту дату у задачи нет повтора")
    return occurrence_date


# ---------------------------------------------------------------- projects
@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(TaskProject).order_by(TaskProject.sort_order, TaskProject.created_at))
    return rows.scalars().all()


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    order = await db.scalar(select(func.coalesce(func.max(TaskProject.sort_order), 0)))
    project = TaskProject(name=payload.name, color=payload.color, sort_order=(order or 0) + 1)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: UUID, payload: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    project = await db.scalar(select(TaskProject).where(TaskProject.id == project_id))
    if not project:
        raise HTTPException(404, "Проект не найден")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(400, f"{field} cannot be null")
        setattr(project, field, value)
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: UUID, db: AsyncSession = Depends(get_db)):
    """Tasks of the project are kept and become "без проекта"."""
    project = await db.scalar(select(TaskProject).where(TaskProject.id == project_id))
    if not project:
        raise HTTPException(404, "Проект не найден")
    for task in (await db.execute(select(Task).where(Task.project_id == project_id))).scalars().all():
        task.project_id = None
    await db.delete(project)
    await db.commit()


# ---------------------------------------------------------------- reading
@router.get("/feed", response_model=TaskFeed)
async def task_feed(
    date_from: date = Query(...),
    date_to: date = Query(...),
    today: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if date_to < date_from:
        raise HTTPException(400, "date_to раньше date_from")
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise HTTPException(400, f"Диапазон не больше {MAX_RANGE_DAYS} дней")
    today = today or _local_today()
    rows = (await db.execute(select(Task).where(or_(
        Task.repeat != "none",
        Task.due_date.is_(None),
        and_(Task.due_date >= date_from, Task.due_date <= date_to),
        and_(Task.is_done.is_(False), Task.due_date < today),
    )))).scalars().all()
    tasks = [_task_dict(t) for t in rows]
    overrides = await _overrides_for(db, [t["id"] for t in tasks if t["repeat"] != "none"])
    return build_feed(tasks, overrides, date_from, date_to, today, _now() - timedelta(hours=KEEP_DONE_HOURS))


@router.get("/list", response_model=List[TaskItem])
async def task_list(today: Optional[date] = Query(None), db: AsyncSession = Depends(get_db)):
    """All open work for the "Проекты" view: open one-off tasks (+ recently done ones) and the next
    occurrence of every recurring series."""
    today = today or _local_today()
    keep_since = _now() - timedelta(hours=KEEP_DONE_HOURS)
    rows = (await db.execute(select(Task).where(or_(
        Task.repeat != "none", Task.is_done.is_(False), Task.done_at >= keep_since,
    )))).scalars().all()
    tasks = [_task_dict(t) for t in rows]
    overrides = await _overrides_for(db, [t["id"] for t in tasks if t["repeat"] != "none"])
    items = []
    for task in tasks:
        if task["repeat"] == "none":
            items.append(single_item(task))
            continue
        upcoming = next_occurrence(task, overrides.get(task["id"], []), today)
        if upcoming:
            items.append(upcoming)
    items.sort(key=lambda i: (i["date"] or date.max, bool(i["is_done"]), i["sort_order"], str(i["created_at"] or "")))
    return items


# ---------------------------------------------------------------- writing
@router.post("", response_model=TaskItem, status_code=201)
async def create_task(payload: TaskCreate, db: AsyncSession = Depends(get_db)):
    await _check_project(db, payload.project_id)
    order = await db.scalar(select(func.coalesce(func.max(Task.sort_order), 0)))
    task = Task(**payload.model_dump(), is_done=False, source="manual", sort_order=(order or 0) + 1)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return _single_item(task)


def _single_item(task: Task) -> dict:
    return single_item(_task_dict(task))


@router.patch("/{task_id}", response_model=TaskItem)
async def update_task(task_id: UUID, payload: TaskUpdate, db: AsyncSession = Depends(get_db)):
    task = await _get_task(db, task_id)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("title", "repeat", "repeat_interval", "repeat_weekdays"):
        if field in changes and changes[field] is None:
            raise HTTPException(400, f"{field} cannot be null")
    if "project_id" in changes:
        await _check_project(db, changes["project_id"])

    current = _task_dict(task)
    merged = {k: current[k] for k in ("title", "notes", "project_id", "due_date", "repeat",
                                      "repeat_interval", "repeat_weekdays", "repeat_until")}
    merged.update(changes)
    try:
        validated = TaskCreate(**merged)
    except ValueError as error:  # pydantic ValidationError is a ValueError
        raise HTTPException(422, str(error))
    data = validated.model_dump()

    rule_changed = any(data[f] != current[f] for f in RULE_FIELDS)
    for field, value in data.items():
        setattr(task, field, value)
    if rule_changed and current["repeat"] != "none":
        # Occurrence dates changed: per-occurrence marks no longer match the series.
        await db.execute(delete(TaskOccurrence).where(TaskOccurrence.task_id == task.id))
    if data["repeat"] != "none":
        task.is_done = False
        task.done_at = None
    await db.commit()
    await db.refresh(task)
    return _single_item(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: UUID, db: AsyncSession = Depends(get_db)):
    """Deletes a one-off task, or a whole recurring series."""
    task = await _get_task(db, task_id)
    await db.delete(task)
    await db.commit()


@router.post("/{task_id}/done", status_code=204)
async def set_done(task_id: UUID, payload: TaskDone, db: AsyncSession = Depends(get_db)):
    task = await _get_task(db, task_id)
    stamp = _now() if payload.done else None
    if (task.repeat or "none") == "none":
        task.is_done = payload.done
        task.done_at = stamp
    else:
        occurrence = _require_occurrence(task, payload.occurrence_date)
        row = await _get_override(db, task.id, occurrence)
        row.done_at = stamp
    await db.commit()


@router.post("/{task_id}/move", status_code=204)
async def move_task(task_id: UUID, payload: TaskMove, db: AsyncSession = Depends(get_db)):
    task = await _get_task(db, task_id)
    if (task.repeat or "none") == "none":
        task.due_date = payload.to_date
    else:
        if payload.to_date is None:
            raise HTTPException(400, "Повтор нельзя перенести в «Без срока»")
        occurrence = _require_occurrence(task, payload.occurrence_date)
        row = await _get_override(db, task.id, occurrence)
        row.moved_to = None if payload.to_date == occurrence else payload.to_date
    await db.commit()


@router.post("/{task_id}/skip", status_code=204)
async def skip_occurrence(task_id: UUID, payload: TaskSkip, db: AsyncSession = Depends(get_db)):
    """Deletes one occurrence of a recurring task; the rest of the series stays."""
    task = await _get_task(db, task_id)
    if (task.repeat or "none") == "none":
        raise HTTPException(400, "Это не повторяющаяся задача: удалите её целиком")
    occurrence = _require_occurrence(task, payload.occurrence_date)
    row = await _get_override(db, task.id, occurrence)
    row.is_skipped = True
    await db.commit()
