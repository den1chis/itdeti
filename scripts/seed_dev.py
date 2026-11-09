"""Test data for the DEV database: admin, 3 students with student-site access, a Python course
(2 modules x 3 lessons, link/text materials, `practice` and `code` tasks) and enrollments.

    python scripts/seed_dev.py

Refuses to run unless DATABASE_URL points to the dev project (scripts/devguard.py).
Idempotent: everything is found by fixed e-mail / names / course title and reused on a second run;
student passwords are reset to the values below on every run.

DEV-ONLY credentials (never use them anywhere else):
    CRM admin:  admin.dev@example.com / devadmin2025
    students:   aliya11 / 111111,  timur22 / 222222,  egor33 / 333333
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402
from scripts.devguard import require_dev_database  # noqa: E402

require_dev_database(settings.DATABASE_URL, settings.APP_ENV)

from sqlalchemy import select  # noqa: E402

from core.database import AsyncSessionLocal, engine  # noqa: E402
from core.security import hash_password  # noqa: E402
from models.lms import LmsCourse, LmsEnrollment, LmsLesson, LmsLessonProgress, LmsMaterial, LmsModule, LmsTask  # noqa: E402
from models.student import Student  # noqa: E402
from models.student_account import StudentAccount  # noqa: E402
from models.user import User  # noqa: E402

SEED_MARK = "seed_dev"
ADMIN = {"email": "admin.dev@example.com", "password": "devadmin2025", "full_name": "Админ (dev)"}
STUDENTS = [
    {"full_name": "Алия Серикова", "login": "aliya11", "password": "111111"},
    {"full_name": "Тимур Ахметов", "login": "timur22", "password": "222222"},
    {"full_name": "Егор Смирнов", "login": "egor33", "password": "333333"},
]
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
            account = await db.scalar(select(StudentAccount).where(StudentAccount.student_id == student.id))
            if account is None:
                account = StudentAccount(student_id=student.id, login=data["login"], is_active=True,
                                         password_hash=hash_password(data["password"]))
                db.add(account)
            else:
                account.login, account.is_active = data["login"], True
                account.password_hash = hash_password(data["password"])
                account.failed_attempts, account.locked_until = 0, None
            await get_or_create(db, LmsEnrollment, {"student_id": student.id, "course_id": course.id},
                                {"status": "active"})

        # A little progress for the first student.
        for lesson, status in ((lessons[0], "done"), (lessons[1], "in_progress")):
            await get_or_create(db, LmsLessonProgress, {"student_id": students[0].id, "lesson_id": lesson.id},
                                {"status": status})

        await db.commit()
    await engine.dispose()
    print(f"seeded: admin {ADMIN['email']}, {len(STUDENTS)} students, course '{COURSE_TITLE}' "
          f"({len(MODULES)} modules, {len(lessons)} lessons)")


if __name__ == "__main__":
    asyncio.run(seed())
