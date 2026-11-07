"""Расчёт расписания курса: на какие даты выпадают уроки курса при недельном расписании ученика.

Чистая логика без базы данных и FastAPI, поэтому её можно проверять обычными тестами.

Правила:
- Уроки курса идут по порядку: 1-й урок курса = 1-е занятие по расписанию, начиная с даты назначения курса.
- Действующий слот (is_active) считается открытым: его valid_until — это предел, который ставит сама система
  по числу уроков курса, поэтому при пересчёте он не учитывается.
- Закрытый слот (удалён или заменён) учитывается только в своём окне valid_from..valid_until:
  занятия, которые он уже дал, остаются в счёте.
- Отменённое занятие уроком курса не считается: его тема переходит на следующее занятие по расписанию,
  поэтому конец курса сдвигается (передаётся в `skip` как пары «слот, дата»).
"""
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any, Collection, List, Optional, Sequence, Tuple

HORIZON_DAYS = 3 * 366  # дальше трёх лет вперёд уроки не раскладываем


@dataclass(frozen=True)
class SlotInfo:
    key: Any
    day_of_week: int  # 0 = понедельник ... 6 = воскресенье
    start_time: time
    valid_from: date
    valid_until: Optional[date]
    is_active: bool
    duration_minutes: int = 60


Entry = Tuple[date, SlotInfo]


def _covers(slot: SlotInfo, day: date) -> bool:
    if day < slot.valid_from:
        return False
    if slot.is_active:
        return True
    return slot.valid_until is not None and day <= slot.valid_until


def plan_course(
    slots: Sequence[SlotInfo],
    anchor: date,
    lessons_total: int,
    horizon_days: int = HORIZON_DAYS,
    skip: Collection[Tuple[Any, date]] = (),
) -> List[Entry]:
    """Первые `lessons_total` занятий по расписанию, начиная с `anchor` (включительно), по порядку.

    `skip` — пары (ключ слота, дата) отменённых занятий: они пропускаются и урок курса уходит на следующее занятие."""
    if lessons_total <= 0 or not slots:
        return []
    by_weekday = {weekday: sorted((s for s in slots if s.day_of_week == weekday), key=lambda s: s.start_time) for weekday in range(7)}
    entries: List[Entry] = []
    day = anchor
    last = anchor + timedelta(days=horizon_days)
    while day <= last and len(entries) < lessons_total:
        for slot in by_weekday[day.weekday()]:
            if _covers(slot, day) and (slot.key, day) not in skip:
                entries.append((day, slot))
                if len(entries) == lessons_total:
                    break
        day += timedelta(days=1)
    return entries


def course_end(entries: Sequence[Entry], lessons_total: int) -> Optional[date]:
    """Дата последнего урока курса; None, если расписание не покрывает все уроки."""
    if lessons_total > 0 and len(entries) == lessons_total:
        return entries[-1][0]
    return None


def pause_window(valid_from: date, today: date, removed_today: bool) -> Optional[date]:
    """Дата, до которой слот расписания остаётся в счёте курса, когда ученика ставят на паузу.

    Занятия до сегодняшнего дня уже прошли и остаются в счёте. Сегодняшнее остаётся, только если оно не удалено
    из календаря. None — слот на счёт курса не влияет (ещё не начался), его можно убрать целиком:
    в базе valid_until не может быть раньше valid_from."""
    until = today - timedelta(days=1) if removed_today else today
    return until if until >= valid_from else None
