from datetime import date, datetime, timedelta, timezone
import uuid

import pytest
from pydantic import ValidationError

from core.task_recurrence import Override, build_feed, expand_task, is_valid_occurrence, next_occurrence, occurrences, single_item
from schemas.tasks import TaskCreate, TaskUpdate

D = date


def task(**kw):
    base = {"id": uuid.uuid4(), "title": "T", "notes": None, "project_id": None, "due_date": None,
            "is_done": False, "done_at": None, "repeat": "none", "repeat_interval": 1,
            "repeat_weekdays": [], "repeat_until": None, "source": "manual", "sort_order": 0,
            "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc)}
    base.update(kw)
    return base


# ------------------------------------------------------------ occurrences
def test_daily_every_n_days_and_range_jump():
    start = D(2026, 1, 1)
    assert occurrences(start, "daily", 3, None, None, D(2026, 1, 1), D(2026, 1, 10)) == [D(2026, 1, 1), D(2026, 1, 4), D(2026, 1, 7), D(2026, 1, 10)]
    # far range: arithmetic jump lands exactly on the series
    got = occurrences(start, "daily", 3, None, None, D(2030, 6, 1), D(2030, 6, 7))
    assert got and all((d - start).days % 3 == 0 for d in got)


def test_weekly_weekdays_interval_and_default_weekday():
    start = D(2026, 11, 4)  # Wednesday
    assert occurrences(start, "weekly", 1, [], None, D(2026, 11, 1), D(2026, 11, 20)) == [D(2026, 11, 4), D(2026, 11, 11), D(2026, 11, 18)]
    # Mon+Thu, every 2nd week; Monday 2 Nov is before start and is excluded
    got = occurrences(start, "weekly", 2, [1, 4], None, D(2026, 11, 1), D(2026, 11, 30))
    assert got == [D(2026, 11, 5), D(2026, 11, 16), D(2026, 11, 19), D(2026, 11, 30)]


def test_monthly_clamps_to_month_end_and_yearly_leap_day():
    got = occurrences(D(2026, 1, 31), "monthly", 1, None, None, D(2026, 1, 1), D(2026, 4, 30))
    assert got == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30)]
    assert occurrences(D(2024, 2, 29), "yearly", 1, None, None, D(2025, 1, 1), D(2028, 12, 31)) == [D(2025, 2, 28), D(2026, 2, 28), D(2027, 2, 28), D(2028, 2, 29)]


def test_until_and_one_off():
    assert occurrences(D(2026, 11, 1), "daily", 1, None, D(2026, 11, 3), D(2026, 11, 1), D(2026, 11, 30)) == [D(2026, 11, 1), D(2026, 11, 2), D(2026, 11, 3)]
    assert occurrences(D(2026, 11, 5), "none", 1, None, None, D(2026, 11, 1), D(2026, 11, 30)) == [D(2026, 11, 5)]
    assert occurrences(D(2026, 12, 5), "none", 1, None, None, D(2026, 11, 1), D(2026, 11, 30)) == []


# ------------------------------------------------------------ overrides
def test_done_moved_and_skipped_occurrences():
    t = task(due_date=D(2026, 11, 2), repeat="daily")
    stamp = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)
    ovs = [Override(D(2026, 11, 2), done_at=stamp),
           Override(D(2026, 11, 3), moved_to=D(2026, 11, 20)),
           Override(D(2026, 11, 4), is_skipped=True)]
    items = expand_task(t, ovs, D(2026, 11, 2), D(2026, 11, 5))
    assert [(i["date"], i["is_done"]) for i in items] == [(D(2026, 11, 2), True), (D(2026, 11, 5), False)]
    moved_in = expand_task(t, ovs, D(2026, 11, 20), D(2026, 11, 20))
    assert [(i["date"], i["occurrence_date"]) for i in moved_in] == [(D(2026, 11, 20), D(2026, 11, 20)), (D(2026, 11, 20), D(2026, 11, 3))]


def test_next_occurrence_skips_done_and_valid_occurrence_check():
    t = task(due_date=D(2026, 11, 2), repeat="weekly", repeat_weekdays=[1])
    ovs = [Override(D(2026, 11, 9), done_at=datetime.now(timezone.utc))]
    assert next_occurrence(t, ovs, D(2026, 11, 9))["date"] == D(2026, 11, 16)
    assert is_valid_occurrence(t, D(2026, 11, 16))
    assert not is_valid_occurrence(t, D(2026, 11, 17))
    assert not is_valid_occurrence(task(due_date=D(2026, 11, 2)), D(2026, 11, 2))


# ------------------------------------------------------------ feed
def test_feed_buckets():
    today = D(2026, 11, 9)
    now = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    keep = now - timedelta(hours=36)
    tasks = [
        task(title="in range", due_date=D(2026, 11, 10)),
        task(title="overdue", due_date=D(2026, 11, 1)),
        task(title="old done", due_date=D(2026, 11, 1), is_done=True, done_at=now - timedelta(days=5)),
        task(title="undated open"),
        task(title="undated done long ago", is_done=True, done_at=now - timedelta(days=3)),
        task(title="undated done today", is_done=True, done_at=now - timedelta(hours=1)),
        task(title="daily", due_date=D(2026, 11, 1), repeat="daily"),
    ]
    feed = build_feed(tasks, {}, D(2026, 11, 9), D(2026, 11, 11), today, keep)
    titles = lambda bucket: [i["title"] for i in feed[bucket]]
    assert sorted(titles("items")) == sorted(["in range", "daily", "daily", "daily"])
    assert titles("overdue") == ["overdue"]  # recurring past occurrences are not carried over
    assert sorted(titles("undated")) == ["undated done today", "undated open"]
    # sorted: dates ascending, open before done within a day
    assert [i["date"] for i in feed["items"]] == sorted(i["date"] for i in feed["items"])


def test_single_item():
    one = single_item(task(due_date=D(2026, 11, 3), is_done=True))
    assert one["date"] == D(2026, 11, 3) and one["is_done"] and one["occurrence_date"] is None
    rec = single_item(task(due_date=D(2026, 11, 3), repeat="daily"))
    assert rec["occurrence_date"] == D(2026, 11, 3) and not rec["is_done"]
    assert single_item(task())["date"] is None


# ------------------------------------------------------------ schemas
def test_task_schema_rules():
    assert TaskCreate(title="  Купить  ").title == "Купить"
    with pytest.raises(ValidationError):
        TaskCreate(title="   ")
    with pytest.raises(ValidationError):
        TaskCreate(title="X", repeat="daily")  # recurring needs a date
    with pytest.raises(ValidationError):
        TaskCreate(title="X", due_date=D(2026, 11, 5), repeat="daily", repeat_until=D(2026, 11, 1))
    with pytest.raises(ValidationError):
        TaskCreate(title="X", due_date=D(2026, 11, 5), repeat="weekly", repeat_weekdays=[8])
    t = TaskCreate(title="X", due_date=D(2026, 11, 5), repeat="weekly", repeat_weekdays=[4, 1, 1])
    assert t.repeat_weekdays == [1, 4]
    assert TaskCreate(title="X", due_date=D(2026, 11, 5), repeat="daily", repeat_weekdays=[1]).repeat_weekdays == []
    assert TaskUpdate(due_date=None).model_dump(exclude_unset=True) == {"due_date": None}
