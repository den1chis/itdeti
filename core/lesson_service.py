from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.lesson import Lesson
from models.schedule import Event
from models.student import Student

LOCAL_TZ = ZoneInfo("Asia/Almaty")


async def complete_overdue_lessons(db: AsyncSession) -> int:
    """Mark finished scheduled lessons as completed and deduct their price once."""
    now = datetime.now(LOCAL_TZ)

    result = await db.execute(
        select(Lesson, Event, Student)
        .join(Event, Event.id == Lesson.event_id)
        .join(Student, Student.id == Lesson.student_id)
        .where(
            Lesson.status == "scheduled",
            Event.end_time <= now,
        )
        .with_for_update(of=(Lesson, Student))
    )

    changed = 0

    for lesson, event, student in result.all():
        lesson.status = "completed"
        lesson.is_attended = True

        if lesson.lesson_type != "trial" and not lesson.balance_deducted:
            student.balance = (
                Decimal(student.balance) - Decimal(lesson.price)
            )
            lesson.balance_deducted = True

        changed += 1

    if changed:
        await db.commit()

    return changed
