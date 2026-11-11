"""Demonstration of the access model without any UI: logs in as every role of the dev seed and prints
a table "what each role sees".

    python scripts/seed_dev.py      # once (idempotent)
    python scripts/demo_roles.py    # runs the app in-process (TestClient), nothing to start

Logs in through the real POST /auth/login with the dev-only seed accounts (scripts/seed_dev.py).
Read-only: only GET requests (plus the logins). Refuses to run against a non-dev database."""
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console: Cyrillic and check marks
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402
from scripts.devguard import require_dev_database  # noqa: E402

require_dev_database(settings.DATABASE_URL, settings.APP_ENV)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from scripts import seed_dev  # noqa: E402

ROLES = [
    ("admin", seed_dev.ADMIN),
    ("teacher A", seed_dev.STAFF["A"]),
    ("teacher B", seed_dev.STAFF["B"]),
    ("methodist", seed_dev.STAFF["M"]),
    ("teacher+methodist", seed_dev.STAFF["TM"]),
]


def code(response) -> str:
    return "200 ✓" if response.status_code == 200 else f"{response.status_code} ✗"


def _write_probe(response) -> str:
    return "allowed" if response.status_code == 422 else f"{response.status_code} ✗"


def main_() -> None:
    with TestClient(main.app) as client:
        admin_login = client.post("/auth/login", json={"email": seed_dev.ADMIN["email"], "password": seed_dev.ADMIN["password"]})
        if admin_login.status_code != 200:
            sys.exit("admin login failed: run scripts/seed_dev.py first")
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        all_students = client.get("/students", headers=admin_headers).json()
        by_name = {s["full_name"]: s["id"] for s in all_students}
        b_student = by_name.get("Егор Смирнов")  # teacher B's student
        a_student = by_name.get("Алия Серикова")  # teacher A's student
        course = next(iter(client.get("/lms/courses", headers=admin_headers).json()), None)

        header = ["", "/students (CRM)", "/finance/summary", "/tasks/projects", "/staff", "/teacher/students",
                  "/teacher/lessons", "Алия (A) card", "Егор (B) card", "Алия password", "LMS catalog read",
                  "LMS course create"]
        rows = []
        for label, creds in ROLES:
            r = client.post("/auth/login", json={"email": creds["email"], "password": creds["password"]})
            if r.status_code != 200:
                rows.append([label, f"login {r.status_code}"] + [""] * (len(header) - 2))
                continue
            h = {"Authorization": f"Bearer {r.json()['access_token']}"}
            teacher_students = client.get("/teacher/students", headers=h)
            lessons = client.get("/teacher/lessons", headers=h)
            subs = sum(1 for l in lessons.json() if l.get("is_substitute")) if lessons.status_code == 200 else 0
            rows.append([
                label,
                code(client.get("/students", headers=h)),
                code(client.get("/finance/summary", headers=h)),
                code(client.get("/tasks/projects", headers=h)),
                code(client.get("/staff", headers=h)),
                f"{len(teacher_students.json())} students" if teacher_students.status_code == 200 else code(teacher_students),
                f"{len(lessons.json())} ({subs} subst.)" if lessons.status_code == 200 else code(lessons),
                code(client.get(f"/teacher/students/{a_student}", headers=h)) if a_student else "-",
                code(client.get(f"/teacher/students/{b_student}", headers=h)) if b_student else "-",
                code(client.get(f"/students/{a_student}/access/password", headers=h)) if a_student else "-",
                code(client.get("/lms/courses", headers=h)),
                # An empty body changes nothing: 422 = access granted (validation failed), 403 = refused.
                _write_probe(client.post("/lms/courses", json={}, headers=h)),
            ])
        if course:
            print(f"(catalog course used: {course['title']})")

    widths = [max(len(str(row[i])) for row in [header] + rows) for i in range(len(header))]
    print(" | ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    print("\nExpected: only admin gets 200 on /students, /finance, /tasks, /staff; teachers see only own students "
          "(A: Алия + Тимур, B: Егор + Дана), a foreign card is 404; teacher B sees Тимур's lesson as a substitution; "
          "the methodist reads the catalog but no students; teacher+methodist has no students of his own.")


if __name__ == "__main__":
    main_()
