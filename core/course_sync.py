"""Связь расписания ученика с курсом.

Расписание ученика (student_schedule) остаётся единственным источником: оно уже само строит календарь.
Здесь курс лишь ограничивает его по числу уроков (valid_until у действующих слотов) и показывает
в карточке курса, какому уроку соответствует какая дата.

Любой сбой здесь не должен ломать остальной API: снаружи вызывайте safe_sync_student / safe_sync_course.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.course_schedule import Entry, SlotInfo, course_end, plan_course
from models.lesson import Lesson
from models.lms import LmsEnrollment, LmsLesson, LmsModule
from models.schedule import Event, StudentScheduleSlot

LOCAL_TZ = ZoneInfo("Asia/Almaty")
log = logging.getLogger(__name__)


@dataclass
class CoursePlan:
    anchor: date
    lessons_total: int
    slots: List[StudentScheduleSlot] = field(default_factory=list)  # все слоты «урок» ученика
    entries: List[Entry] = field(default_factory=list)  # по одному занятию на урок курса, по порядку
    end: Optional[date] = None  # дата последнего урока, если расписание покрывает весь курс

    @property
    def active_slots(self) -> List[StudentScheduleSlot]:
        return [slot for slot in self.slots if slot.is_active]


def _anchor(started_at: datetime) -> date:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=LOCAL_TZ)
    return started_at.astimezone(LOCAL_TZ).date()


def _nominal_start(day: date, slot: SlotInfo) -> datetime:
    return datetime.combine(day, slot.start_time).replace(tzinfo=LOCAL_TZ)


async def _lessons_total(db: AsyncSession, course_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count(LmsLesson.id))
            .select_from(LmsLesson)
            .join(LmsModule, LmsModule.id == LmsLesson.module_id)
            .where(LmsModule.course_id == course_id)
        )
        or 0
    )


async def load_plan(db: AsyncSession, student_id: UUID, started_at: datetime, lessons_total: int) -> CoursePlan:
    anchor = _anchor(started_at)
    slots = list(
        (
            await db.execute(
                select(StudentScheduleSlot).where(
                    StudentScheduleSlot.student_id == student_id, StudentScheduleSlot.lesson_kind == "lesson"
                )
            )
        ).scalars().all()
    )
    infos = [
        SlotInfo(
            key=slot.id,
            day_of_week=slot.day_of_week,
            start_time=slot.start_time,
            valid_from=slot.valid_from,
            valid_until=slot.valid_until,
            is_active=bool(slot.is_active),
        )
        for slot in slots
    ]
    entries = plan_course(infos, anchor, lessons_total)
    return CoursePlan(anchor=anchor, lessons_total=lessons_total, slots=slots, entries=entries, end=course_end(entries, lessons_total))


async def lesson_dates(db: AsyncSession, student_id: UUID, plan: CoursePlan) -> List[Tuple[datetime, Optional[str]]]:
    """Для каждого урока курса (по порядку): дата и время занятия и статус занятия в календаре.

    Если занятие в календаре перенесено, берётся его фактическое время; статус возвращается, только когда он
    не «запланировано» (проведено / отменено / перенесено)."""
    if not plan.entries:
        return []
    nominal = [_nominal_start(day, slot) for day, slot in plan.entries]
    rows = await db.execute(
        select(Lesson.schedule_slot_id, Lesson.original_start_time, Lesson.status, Event.start_time)
        .join(Event, Event.id == Lesson.event_id)
        .where(
            Lesson.student_id == student_id,
            Lesson.schedule_slot_id.in_([slot.key for _, slot in plan.entries]),
            Lesson.original_start_time.in_(nominal),
        )
    )
    found: Dict[Tuple[UUID, datetime], Tuple[datetime, str]] = {}
    for slot_id, original, status, event_start in rows.all():
        found[(slot_id, original)] = (event_start, status)
    result: List[Tuple[datetime, Optional[str]]] = []
    for (_, slot), start in zip(plan.entries, nominal):
        actual = found.get((slot.key, start))
        if actual:
            result.append((actual[0], actual[1] if actual[1] != "scheduled" else None))
        else:
            result.append((start, None))
    return result


async def safe_plan_dates(
    db: AsyncSession, student_id: UUID, started_at: datetime, lessons_total: int
) -> Tuple[Optional[CoursePlan], List[Tuple[datetime, Optional[str]]]]:
    """План и даты уроков для показа в карточке. При сбое возвращает пустой результат, а не ошибку:
    карточка курса должна открываться и без дат."""
    try:
        async with db.begin_nested():
            plan = await load_plan(db, student_id, started_at, lessons_total)
            dates = await lesson_dates(db, student_id, plan)
        return plan, dates
    except Exception:
        log.exception("Course plan failed for student %s", student_id)
        return None, []


async def _release(db: AsyncSession, student_id: UUID) -> None:
    """Курса нет или он без уроков: расписание снова не ограничено по числу занятий."""
    slots = (
        await db.execute(
            select(StudentScheduleSlot).where(
                StudentScheduleSlot.student_id == student_id,
                StudentScheduleSlot.lesson_kind == "lesson",
                StudentScheduleSlot.is_active.is_(True),
                StudentScheduleSlot.valid_until.is_not(None),
            )
        )
    ).scalars().all()
    for slot in slots:
        slot.valid_until = None


async def sync_student_schedule(db: AsyncSession, student_id: UUID) -> None:
    """Ставит у действующих слотов ученика предел valid_until = дата последнего урока курса
    и убирает из календаря будущие занятия сверх этого числа."""
    row = (
        await db.execute(
            select(LmsEnrollment.course_id, LmsEnrollment.started_at, LmsEnrollment.status)
            .where(LmsEnrollment.student_id == student_id)
            .order_by(LmsEnrollment.started_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        await _release(db, student_id)
        return
    course_id, started_at, status = row
    if status == "finished":
        return
    total = await _lessons_total(db, course_id)
    if total <= 0:
        await _release(db, student_id)
        return
    plan = await load_plan(db, student_id, started_at, total)
    if plan.end is None:
        await _release(db, student_id)
        return

    for slot in plan.active_slots:
        # слот, который начинается уже после конца курса, не трогаем: valid_until не может быть раньше valid_from
        if plan.end >= slot.valid_from and slot.valid_until != plan.end:
            slot.valid_until = plan.end

    slot_ids = [slot.id for slot in plan.slots]
    if not slot_ids:
        return
    cutoff = datetime.combine(plan.end + timedelta(days=1), time.min).replace(tzinfo=LOCAL_TZ)
    excess = await db.execute(
        select(Lesson, Event)
        .join(Event, Event.id == Lesson.event_id)
        .where(
            Lesson.student_id == student_id,
            Lesson.schedule_slot_id.in_(slot_ids),
            Lesson.status == "scheduled",
            Lesson.original_start_time.is_not(None),
            Lesson.original_start_time >= cutoff,
        )
    )
    for lesson, event in excess.all():
        await db.delete(lesson)
        await db.delete(event)


async def safe_sync_student(db: AsyncSession, student_id: UUID) -> None:
    try:
        async with db.begin_nested():
            await sync_student_schedule(db, student_id)
    except Exception:  # синхронизация вторична: основное действие пользователя не должно из-за неё падать
        log.exception("Course schedule sync failed for student %s", student_id)


async def safe_sync_course(db: AsyncSession, course_id: UUID) -> None:
    """Пересчитать расписание всех учеников курса (число уроков курса изменилось)."""
    try:
        student_ids = (await db.execute(select(LmsEnrollment.student_id).where(LmsEnrollment.course_id == course_id))).scalars().all()
    except Exception:
        log.exception("Course schedule sync failed for course %s", course_id)
        return
    for student_id in student_ids:
        await safe_sync_student(db, student_id)
