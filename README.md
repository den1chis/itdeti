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

Run `supabase/migrations/001_reset_school_schema.sql` in Supabase SQL Editor before the first start of the new version.

The migration preserves only:

- `users`
- `refresh_tokens`
- `incoming_notifications`

All school/business tables are recreated.

## Environment

Copy `.env.example` to `.env` locally and fill in real values. Never commit `.env`.

For Supabase use a connection string based on the Session Pooler (`:5432`), not the Transaction Pooler (`:6543`).

## Local run

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

API documentation:

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
