"""Pure logic for tasks: recurrence expansion and building the task feed.

No database, no FastAPI: everything here takes plain values / dicts, so it is fully unit-tested
(tests/test_task_recurrence.py). The router only loads rows and calls these functions.

Model
-----
A task has an optional ``due_date``. A *recurring* task (``repeat`` != "none") must have one: it is
the date of the first occurrence. Occurrences are never stored; they are expanded on the fly.
Per-occurrence changes live in overrides keyed by the ORIGINAL occurrence date:
    done_at   - this occurrence was completed
    moved_to  - this occurrence is shown on another date
    is_skipped- this occurrence was deleted
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

REPEATS = ("none", "daily", "weekly", "monthly", "yearly")
MAX_ITERATIONS = 5000  # hard stop for any expansion loop


def _add_months(base: date, months: int, day: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def occurrences(
    start: date,
    repeat: str,
    interval: int,
    weekdays: Optional[Iterable[int]],
    until: Optional[date],
    range_from: date,
    range_to: date,
) -> list[date]:
    """Dates of a series that fall in [range_from, range_to] (both inclusive), ascending.

    weekdays: ISO numbers 1=Mon..7=Sun, used only for weekly; empty -> weekday of ``start``.
    """
    if repeat == "none" or start is None:
        return [start] if start is not None and range_from <= start <= range_to else []
    interval = max(1, int(interval or 1))
    end = min(range_to, until) if until else range_to
    lo = max(range_from, start)
    if lo > end:
        return []
    result: list[date] = []

    if repeat == "daily":
        offset = (lo - start).days
        k = -(-offset // interval)  # ceil
        current = start + timedelta(days=k * interval)
        while current <= end and len(result) < MAX_ITERATIONS:
            result.append(current)
            current += timedelta(days=interval)
        return result

    if repeat == "weekly":
        days = sorted({int(d) for d in (weekdays or []) if 1 <= int(d) <= 7}) or [start.isoweekday()]
        week0 = start - timedelta(days=start.isoweekday() - 1)  # Monday of the start week
        weeks_to_lo = (lo - week0).days // 7
        k = (weeks_to_lo // interval) * interval
        guard = 0
        while guard < MAX_ITERATIONS:
            guard += 1
            monday = week0 + timedelta(weeks=k)
            if monday > end:
                break
            for d in days:
                current = monday + timedelta(days=d - 1)
                if lo <= current <= end:
                    result.append(current)
            k += interval
        return result

    if repeat in ("monthly", "yearly"):
        step = interval if repeat == "monthly" else interval * 12
        months_to_lo = (lo.year - start.year) * 12 + (lo.month - start.month)
        k = max(0, (months_to_lo // step) * step - step)
        guard = 0
        while guard < MAX_ITERATIONS:
            guard += 1
            current = _add_months(start, k, start.day)
            if current > end:
                break
            if current >= lo:
                result.append(current)
            k += step
        return result

    raise ValueError(f"unknown repeat: {repeat}")


@dataclass
class Override:
    occurrence_date: date
    done_at: Optional[datetime] = None
    moved_to: Optional[date] = None
    is_skipped: bool = False


def _task_fields(task: dict) -> dict:
    return {
        "task_id": task["id"],
        "title": task["title"],
        "notes": task.get("notes"),
        "project_id": task.get("project_id"),
        "repeat": task.get("repeat") or "none",
        "repeat_interval": task.get("repeat_interval") or 1,
        "repeat_weekdays": list(task.get("repeat_weekdays") or []),
        "repeat_until": task.get("repeat_until"),
        "series_start": task.get("due_date"),
        "source": task.get("source") or "manual",
        "sort_order": task.get("sort_order") or 0,
        "created_at": task.get("created_at"),
    }


def _sort_key(item: dict):
    created = item.get("created_at")
    return (item["date"] or date.max, bool(item["is_done"]), item["sort_order"], str(created or ""), str(item["task_id"]))


def expand_task(task: dict, overrides: list[Override], range_from: date, range_to: date) -> list[dict]:
    """Feed items of one task shown on dates within [range_from, range_to]."""
    base = _task_fields(task)
    repeat = base["repeat"]

    if repeat == "none":
        due = task.get("due_date")
        if due is None or not (range_from <= due <= range_to):
            return []
        return [{**base, "date": due, "occurrence_date": None,
                 "is_done": bool(task.get("is_done")), "done_at": task.get("done_at")}]

    by_date = {o.occurrence_date: o for o in overrides}
    items: list[dict] = []
    seen: set[date] = set()

    # Regular occurrences in range (unless skipped or moved away).
    for d in occurrences(task["due_date"], repeat, base["repeat_interval"], base["repeat_weekdays"],
                         task.get("repeat_until"), range_from, range_to):
        o = by_date.get(d)
        if o and (o.is_skipped or (o.moved_to and o.moved_to != d)):
            continue
        items.append({**base, "date": d, "occurrence_date": d,
                      "is_done": bool(o and o.done_at), "done_at": o.done_at if o else None})
        seen.add(d)

    # Occurrences moved INTO the range from any original date.
    for o in overrides:
        if o.is_skipped or not o.moved_to or o.moved_to == o.occurrence_date:
            continue
        if range_from <= o.moved_to <= range_to:
            items.append({**base, "date": o.moved_to, "occurrence_date": o.occurrence_date,
                          "is_done": bool(o.done_at), "done_at": o.done_at})
    return items


def single_item(task: dict) -> dict:
    """The task as one feed item: one-off -> its date (or None); recurring -> its first occurrence."""
    base = _task_fields(task)
    due = task.get("due_date")
    if base["repeat"] == "none":
        return {**base, "date": due, "occurrence_date": None,
                "is_done": bool(task.get("is_done")), "done_at": task.get("done_at")}
    return {**base, "date": due, "occurrence_date": due, "is_done": False, "done_at": None}


def is_valid_occurrence(task: dict, occurrence_date: date) -> bool:
    """True if ``occurrence_date`` is a real (original) date of the recurring series."""
    if (task.get("repeat") or "none") == "none" or task.get("due_date") is None:
        return False
    return occurrence_date in occurrences(task["due_date"], task["repeat"], task.get("repeat_interval") or 1,
                                          task.get("repeat_weekdays"), task.get("repeat_until"),
                                          occurrence_date, occurrence_date)


def next_occurrence(task: dict, overrides: list[Override], today: date, horizon_days: int = 800) -> Optional[dict]:
    """First not-done occurrence on or after ``today`` (for the projects list)."""
    items = expand_task(task, overrides, today, today + timedelta(days=horizon_days))
    items = [i for i in items if not i["is_done"]]
    items.sort(key=_sort_key)
    return items[0] if items else None


def build_feed(tasks: list[dict], overrides_by_task: dict, range_from: date, range_to: date,
               today: date, keep_done_since: datetime) -> dict:
    """Everything the UI needs for a date range.

    items   - tasks/occurrences dated within the range
    undated - tasks without a date (not done, or done after ``keep_done_since``)
    overdue - one-off tasks dated before ``today`` and still open
    """
    items: list[dict] = []
    undated: list[dict] = []
    overdue: list[dict] = []
    for task in tasks:
        ovs = overrides_by_task.get(task["id"], [])
        items.extend(expand_task(task, ovs, range_from, range_to))
        repeat = task.get("repeat") or "none"
        if repeat != "none":
            continue
        if task.get("due_date") is None:
            done_at = task.get("done_at")
            if not task.get("is_done") or (done_at and done_at >= keep_done_since):
                undated.append({**_task_fields(task), "date": None, "occurrence_date": None,
                                "is_done": bool(task.get("is_done")), "done_at": done_at})
        elif not task.get("is_done") and task["due_date"] < today:
            overdue.append({**_task_fields(task), "date": task["due_date"], "occurrence_date": None,
                            "is_done": False, "done_at": None})
    for bucket in (items, undated, overdue):
        bucket.sort(key=_sort_key)
    return {"items": items, "undated": undated, "overdue": overdue}
