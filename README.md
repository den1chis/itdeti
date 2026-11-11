# itdeti AI System — Backend

FastAPI backend for the itdeti programming school management system.

## Stack

- Python 3.12
- FastAPI
- SQLAlchemy 2.x async
- asyncpg
- PostgreSQL / Supabase
- JWT + direct bcrypt
- Docker / Render

## Database

The application is designed for the Supabase **Session Pooler** on port `5432`.
The SQLAlchemy engine uses:

```python
connect_args={"ssl": "require"}
```

### Migrations

- All migrations live in `supabase/migrations/` (list and notes: `supabase/migrations/README.md`).
- They are applied **manually, one by one, in number order**, in the Supabase SQL Editor of the project.
  Only the migrations that have not been applied to that database yet.
- **Never run `001_reset_school_schema.sql` on a live database.** It drops and recreates all school
  tables (students, lessons, payments, schedule, ...): every row in them is lost. It was used once to create
  the schema of the current version and is kept only as history.
- `010_clear_student_course_text.sql` is a one-time cleanup: running it again erases the course names of students.
- New migrations are additive only (no `DROP`, `RENAME`, type changes, `DELETE`), every new table with
  `ENABLE ROW LEVEL SECURITY`. `scripts/apply_migration.py <number>` checks this and applies a migration
  to the **dev** database only (it refuses any other database).
- Deploy order: migrations first, then the code (see `docs/DEPLOY_RUNBOOK.md`).

## Environment

Copy `.env.example` to `.env` locally and fill in real values. Never commit `.env`.

Security related variables (details in `.env.example` and `docs/DEPLOY_RUNBOOK.md`):

- `ENABLE_DOCS` - `/docs`, `/redoc`, `/openapi.json` exist only with `ENABLE_DOCS=true`; leave it unset in production.
- `CORS_ORIGINS`, `CORS_EXTRA_ORIGINS` - explicit list of allowed browser origins (default: GitHub Pages
  `https://den1chis.github.io` and the local ports 5500/5501).
- `BEHIND_PROXY`, `TRUSTED_PROXY_HOPS` - real client IP for the student login rate limit behind Render's proxy.
- `STUDENT_PASSWORD_KEY` - Fernet key for viewable student passwords.
- `APP_ENV` - default `production` (safe: also when the variable is missing). Locally `dev`. Never `development`
  on a real database: that value runs `create_all` on start (tables without migrations and RLS) and logs every SQL query.

For Supabase use a connection string based on the Session Pooler (`:5432`), not the Transaction Pooler (`:6543`).

## Local run

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

API documentation (only with `ENABLE_DOCS=true` in `.env`):

`http://localhost:8000/docs`

## Main API

### Auth

- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`

### Students

- `POST /students`
- `GET /students`
- `GET /students/{id}`
- `PATCH /students/{id}`
- `DELETE /students/{id}`
- `POST /students/{id}/schedule`
- `GET /students/{id}/schedule`
- `PATCH /students/{id}/schedule/{slot_id}`
- `DELETE /students/{id}/schedule/{slot_id}`
- `GET /students/{id}/balance`

### Lessons and calendar

- `POST /lessons`
- `GET /lessons`
- `PATCH /lessons/{id}`
- `GET /schedule/today`
- `GET /schedule/week`
- `POST /events`
- `GET /events`
- `PATCH /events/{id}`
- `DELETE /events/{id}`

### Finance

- `POST /payments`
- `GET /payments`
- `POST /expenses`
- `GET /expenses`
- `GET /finance/summary`
- `GET /finance/transactions`

### Notifications

- `POST /notifications`
- `GET /notifications`
- `POST /notifications/{id}/confirm`

Kaspi/WhatsApp notifications are stored and parsed, but payment creation remains an explicit financial operation. AI automation can be connected later through Claude.

## Access model (roles)

Every route has an explicit access class (`core/access.py`); see `docs/ROUTES_ACCESS.md` for the full table.

- Roles: `admin`, `teacher`, `methodist`; a user has a set of roles (`users.roles`, migration 013), permissions add up.
- All management routes above (students, parents, lessons, calendar, events, finance, notifications, tasks) are **admin only**.
- Teacher: read-only `/teacher/*` (own students, own lessons, substitutions; no money fields), LMS catalog read,
  notes and student-site access of own students (view password, reset, unlock). Foreign data -> 404.
- Methodist: LMS catalog read and write.
- Admin: `/staff` (staff users, roles, dismissal rule), `PUT /students/{id}/teacher`, `PUT /lessons/{id}/teacher`.
- `STUDENT_PASSWORD_KEY` (Fernet) enables viewing student passwords; without it everything else works.

Migrations live in `supabase/migrations/`; apply them to the dev database with `python scripts/apply_migration.py <number>`.
Deploy order: migrations first, then the code.
