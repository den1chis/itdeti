-- 001_student_accounts.sql — student site (itdetiLearn), night 1.
-- Additive only: one new table, one new nullable column, one new enum value.
-- Apply to the DEV database only:  python scripts/apply_migration.py migrations/001_student_accounts.sql
-- (or paste into the Supabase SQL editor of the itdeti-dev project).

BEGIN;

-- Student login for the student site. Separate from users (teacher/admin accounts):
-- a student account gives access to the student's own lessons and nothing else.
CREATE TABLE IF NOT EXISTS student_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL UNIQUE REFERENCES students(id) ON DELETE CASCADE,
    login TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,              -- bcrypt; the plain password is never stored
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,                 -- 5 wrong passwords -> locked for 15 minutes
    last_login_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT student_accounts_login_format CHECK (login ~ '^[a-z0-9_.-]{3,32}$'),
    CONSTRAINT student_accounts_failed_attempts_nonneg CHECK (failed_attempts >= 0)
);

ALTER TABLE student_accounts ENABLE ROW LEVEL SECURITY;

-- Groundwork for several teachers: who teaches the student (nullable, nothing uses it yet).
ALTER TABLE students ADD COLUMN IF NOT EXISTS teacher_id UUID NULL REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_students_teacher_id ON students(teacher_id);

COMMIT;

-- New role. Kept outside the transaction block: ALTER TYPE ... ADD VALUE cannot be used
-- inside a transaction on older PostgreSQL versions, and the new value cannot be used
-- in the same transaction that adds it.
ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'methodist';
