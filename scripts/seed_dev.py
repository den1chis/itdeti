"""Test data for the DEV database: admin, 3 students with student-site access, a Python course
(2 modules x 3 lessons, link/text materials, `practice` and `code` tasks) and enrollments.

    python scripts/seed_dev.py

Refuses to run unless DATABASE_URL points to the dev project (scripts/devguard.py).
Idempotent: everything is found by fixed e-mail / names / course title and reused on a second run;
student passwords are reset to the values below on every run.

DEV-ONLY credentials (never use them anywhere else):
    CRM admin:  admin.dev@example.com / devadmin2025
    students:   aliya11 / 111111,  timur22 / 222222,  egor33 / 333333,  dana44 / 444444,  maks55 / 555555

Night 2 (access model) — staff, DEV-ONLY:
    teacher A:              teacher.a.dev@example.com     / devteacherA25   students: Алия, Тимур
    teacher B:              teacher.b.dev@example.com     / devteacherB25   students: Егор, Дана
    methodist:              methodist.dev@example.com     / devmethod25     (catalog only)
    teacher + methodist:    teachmeth.dev@example.com     / devteachmeth25  (no students)
    Максим has no teacher. One future lesson of Тимур (teacher A) is substituted by teacher B.
    Student passwords are stored encrypted too (when STUDENT_PASSWORD_KEY is set), so they can be viewed.
"""
import asyncio
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402
from scripts.devguard import require_dev_database  # noqa: E402

require_dev_database(settings.DATABASE_URL, settings.APP_ENV)

from sqlalchemy import select  # noqa: E402

from core.database import AsyncSessionLocal, engine  # noqa: E402
from core.security import hash_password  # noqa: E402
from core.student_secrets import encrypt_password  # noqa: E402
from models.lesson import Lesson  # noqa: E402
from models.parent import Parent, StudentParent  # noqa: E402
import models.recurring_event  # noqa: E402,F401  (events.recurring_event_id FK target)
from models.schedule import Event, StudentScheduleSlot  # noqa: E402
from models.lms import LmsCourse, LmsEnrollment, LmsLesson, LmsLessonProgress, LmsMaterial, LmsModule, LmsTask  # noqa: E402
from models.student import Student  # noqa: E402
from models.student_account import StudentAccount  # noqa: E402
from models.user import User  # noqa: E402

SEED_MARK = "seed_dev"
TZ = ZoneInfo("Asia/Almaty")
ADMIN = {"email": "admin.dev@example.com", "password": "devadmin2025", "full_name": "Админ (dev)"}
STUDENTS = [
    {"full_name": "Алия Серикова", "login": "aliya11", "password": "111111", "teacher": "A"},
    {"full_name": "Тимур Ахметов", "login": "timur22", "password": "222222", "teacher": "A"},
    {"full_name": "Егор Смирнов", "login": "egor33", "password": "333333", "teacher": "B"},
    {"full_name": "Дана Каримова", "login": "dana44", "password": "444444", "teacher": "B"},
    {"full_name": "Максим Ли", "login": "maks55", "password": "555555", "teacher": None},
]
STAFF = {
    "A": {"email": "teacher.a.dev@example.com", "password": "devteacherA25", "full_name": "Преподаватель А (dev)", "roles": ["teacher"]},
    "B": {"email": "teacher.b.dev@example.com", "password": "devteacherB25", "full_name": "Преподаватель Б (dev)", "roles": ["teacher"]},
    "M": {"email": "methodist.dev@example.com", "password": "devmethod25", "full_name": "Методист (dev)", "roles": ["methodist"]},
    "TM": {"email": "teachmeth.dev@example.com", "password": "devteachmeth25", "full_name": "Преподаватель-методист (dev)", "roles": ["teacher", "methodist"]},
}
PARENTS = {"Алия Серикова": ("Сауле Серикова", "+77010000011"), "Егор Смирнов": ("Ольга Смирнова", "+77010000033")}
SUBSTITUTION_TOPIC = "Замена (dev seed)"
COURSE_TITLE = "Python (dev seed)"
MODULES = [
    {
        "title": "Модуль 1. Первые программы",
        "lessons": [
            ("Знакомство с Python", "Что такое программа и как её запустить"),
            ("Переменные", "Храним числа и строки"),
            ("Ввод и вывод", "input() и print()"),
        ],
    },
    {
        "title": "Модуль 2. Условия и циклы",
        "lessons": [
            ("Условие if", "Программа принимает решения"),
            ("Цикл for", "Повторяем действия"),
            ("Цикл while", "Повторяем, пока условие верно"),
        ],
    },
]


async def get_or_create(db, model, where: dict, values: dict):
    query = select(model)
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    obj = await db.scalar(query)
    if obj is None:
        obj = model(**where, **values)
        db.add(obj)
        await db.flush()
    return obj


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.email == ADMIN["email"]))
        if admin is None:
            admin = User(email=ADMIN["email"], full_name=ADMIN["full_name"], role="admin", is_active=True,
                         password_hash=hash_password(ADMIN["password"]))
            db.add(admin)
        else:
            admin.password_hash = hash_password(ADMIN["password"])
            admin.role, admin.is_active = "admin", True
        admin.roles = ["admin"]

        staff = {}
        for key, data in STAFF.items():
            user = await db.scalar(select(User).where(User.email == data["email"]))
            if user is None:
                user = User(email=data["email"], full_name=data["full_name"], password_hash="-")
                db.add(user)
            user.password_hash = hash_password(data["password"])
            user.role, user.roles, user.is_active = data["roles"][0], list(data["roles"]), True
            staff[key] = user
        await db.flush()

        course = await get_or_create(
            db, LmsCourse, {"title": COURSE_TITLE},
            {"course_type": "code", "description": "Тестовый курс для локальной разработки"},
        )
        lessons = []
        for m_index, module_data in enumerate(MODULES):
            module = await get_or_create(db, LmsModule, {"course_id": course.id, "title": module_data["title"]},
                                         {"sort_order": m_index})
            for l_index, (title, description) in enumerate(module_data["lessons"]):
                lesson = await get_or_create(db, LmsLesson, {"module_id": module.id, "title": title},
                                             {"description": description, "duration_min": 60, "sort_order": l_index})
                lessons.append(lesson)
                n = len(lessons)
                await get_or_create(db, LmsMaterial, {"lesson_id": lesson.id, "title": f"Презентация к уроку {n}"},
                                    {"kind": "link", "sort_order": 0,
                                     "url": "https://docs.python.org/3/tutorial/introduction.html"})
                await get_or_create(db, LmsMaterial, {"lesson_id": lesson.id, "title": "Конспект"},
                                    {"kind": "text", "sort_order": 1,
                                     "body": f"Главное из урока «{title}»: {description.lower()}."})
                await get_or_create(db, LmsTask, {"lesson_id": lesson.id, "title": "Практика"},
                                    {"kind": "practice", "sort_order": 0,
                                     "statement": "Повтори пример из презентации и покажи результат преподавателю.",
                                     "hint": "Если не получается, перечитай конспект."})
                await get_or_create(db, LmsTask, {"lesson_id": lesson.id, "title": "Задача с кодом"},
                                    {"kind": "code", "sort_order": 1,
                                     "statement": "Напиши программу, которая выводит Hello, World!",
                                     "starter_code": "# напиши код здесь\n",
                                     "hint": "Используй print()",
                                     # Hidden autograder data: must never reach the student site.
                                     "tests": {"cases": [{"input": "", "expected_output": "Hello, World!\n"}]}})

        students = []
        for data in STUDENTS:
            student = await get_or_create(db, Student, {"full_name": data["full_name"]},
                                          {"lesson_price": 5000, "balance": 0, "is_active": True,
                                           "notes": SEED_MARK, "course": COURSE_TITLE})
            students.append(student)
            student.teacher_id = staff[data["teacher"]].id if data["teacher"] else None
            account = await db.scalar(select(StudentAccount).where(StudentAccount.student_id == student.id))
            if account is None:
                account = StudentAccount(student_id=student.id, login=data["login"], is_active=True,
                                         password_hash=hash_password(data["password"]))
                db.add(account)
            else:
                account.login, account.is_active = data["login"], True
                account.password_hash = hash_password(data["password"])
                account.failed_attempts, account.locked_until = 0, None
            account.password_enc = encrypt_password(data["password"])
            if data["full_name"] in PARENTS:
                name, phone = PARENTS[data["full_name"]]
                parent = await get_or_create(db, Parent, {"full_name": name}, {"phone": phone, "whatsapp_phone": phone})
                await get_or_create(db, StudentParent, {"student_id": student.id, "parent_id": parent.id}, {})
            await get_or_create(db, LmsEnrollment, {"student_id": student.id, "course_id": course.id},
                                {"status": "active"})

        # A little progress for the first student.
        for lesson, status in ((lessons[0], "done"), (lessons[1], "in_progress")):
            await get_or_create(db, LmsLessonProgress, {"student_id": students[0].id, "lesson_id": lesson.id},
                                {"status": status})

        # Weekly schedule for two students (so /teacher/students/{id} and /teacher/lessons have data).
        for student, day, hour in ((students[0], 0, 16), (students[2], 2, 17)):
            await get_or_create(db, StudentScheduleSlot,
                                {"student_id": student.id, "day_of_week": day, "start_time": time(hour, 0)},
                                {"duration_minutes": 60, "is_active": True})
        # One substitution: a future lesson of Тимур (teacher A) is given by teacher B.
        timur = students[1]
        now = datetime.now(TZ)
        future = await db.scalar(
            select(Lesson).join(Event, Event.id == Lesson.event_id).where(
                Lesson.student_id == timur.id, Lesson.topic == SUBSTITUTION_TOPIC,
                Lesson.status == "scheduled", Event.start_time > now))
        if future is None:
            start = datetime.combine(now.date() + timedelta(days=3), time(15, 0), tzinfo=TZ)
            event = Event(title=f"{timur.full_name} — Урок", event_type="personal", start_time=start,
                          end_time=start + timedelta(hours=1))
            db.add(event)
            await db.flush()
            future = Lesson(student_id=timur.id, event_id=event.id, lesson_kind="lesson", lesson_type="regular",
                            status="scheduled", topic=SUBSTITUTION_TOPIC, price=timur.lesson_price)
            db.add(future)
        future.teacher_id = staff["B"].id
        await db.commit()
    await engine.dispose()
    print(f"seeded: admin {ADMIN['email']}, {len(STAFF)} staff users, {len(STUDENTS)} students, "
          f"course '{COURSE_TITLE}' ({len(MODULES)} modules, {len(lessons)} lessons), 1 substitution")


if __name__ == "__main__":
    asyncio.run(seed())
