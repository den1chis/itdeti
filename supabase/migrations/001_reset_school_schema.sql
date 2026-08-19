BEGIN;

-- Preserve: users, refresh_tokens, incoming_notifications.
-- Rebuild all school/business tables from scratch.

DROP TABLE IF EXISTS lessons CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS expenses CASCADE;
DROP TABLE IF EXISTS student_schedule CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS student_parents CASCADE;
DROP TABLE IF EXISTS parents CASCADE;
DROP TABLE IF EXISTS students CASCADE;
DROP TABLE IF EXISTS subscriptions CASCADE;
DROP TABLE IF EXISTS courses CASCADE;

DROP TYPE IF EXISTS lesson_status CASCADE;
DROP TYPE IF EXISTS lesson_type CASCADE;
DROP TYPE IF EXISTS event_type CASCADE;
DROP TYPE IF EXISTS payment_method CASCADE;
DROP TYPE IF EXISTS expense_category CASCADE;
DROP TYPE IF EXISTS expense_payment_method CASCADE;
DROP TYPE IF EXISTS payment_status CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE event_type AS ENUM ('personal', 'meeting', 'reminder');
CREATE TYPE lesson_type AS ENUM ('regular', 'trial', 'extra');
CREATE TYPE lesson_status AS ENUM ('scheduled', 'completed', 'cancelled', 'rescheduled');
CREATE TYPE payment_method AS ENUM ('kaspi', 'cash', 'transfer', 'other');
CREATE TYPE expense_category AS ENUM ('car', 'food', 'utilities', 'salary', 'equipment', 'other');
CREATE TYPE expense_payment_method AS ENUM ('kaspi', 'cash', 'transfer', 'other');

CREATE TABLE students (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    full_name TEXT NOT NULL,
    birth_date DATE NULL,
    level TEXT NULL DEFAULT 'beginner',
    notes TEXT NULL,
    balance NUMERIC(10, 2) NOT NULL DEFAULT 0,
    lesson_price NUMERIC(10, 2) NOT NULL CHECK (lesson_price > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_students_user_id ON students(user_id);
CREATE INDEX ix_students_active ON students(is_active);

CREATE TABLE parents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    whatsapp_phone TEXT NULL,
    email TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_parents_user_id ON parents(user_id);

CREATE TABLE student_parents (
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    parent_id UUID NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, parent_id)
);

CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    event_type event_type NOT NULL DEFAULT 'personal',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    location TEXT NULL,
    notes TEXT NULL,
    is_cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_events_time CHECK (end_time > start_time)
);

CREATE INDEX ix_events_start_time ON events(start_time);

CREATE TABLE student_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes BETWEEN 15 AND 480),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_until DATE NULL,
    CONSTRAINT ck_schedule_valid_dates CHECK (valid_until IS NULL OR valid_until >= valid_from)
);

CREATE INDEX ix_student_schedule_student_id ON student_schedule(student_id);
CREATE INDEX ix_student_schedule_active ON student_schedule(is_active);

CREATE TABLE lessons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    event_id UUID NULL UNIQUE REFERENCES events(id) ON DELETE SET NULL,
    lesson_type lesson_type NOT NULL DEFAULT 'regular',
    status lesson_status NOT NULL DEFAULT 'scheduled',
    topic TEXT NULL,
    is_attended BOOLEAN NULL,
    teacher_notes TEXT NULL,
    price NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (price >= 0),
    balance_deducted BOOLEAN NOT NULL DEFAULT FALSE,
    schedule_slot_id UUID NULL REFERENCES student_schedule(id) ON DELETE SET NULL,
    original_start_time TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_lessons_student_id ON lessons(student_id);
CREATE INDEX ix_lessons_schedule_slot_id ON lessons(schedule_slot_id);
CREATE INDEX ix_lessons_status ON lessons(status);
CREATE UNIQUE INDEX uq_lessons_slot_original_time
    ON lessons(schedule_slot_id, original_start_time)
    WHERE schedule_slot_id IS NOT NULL AND original_start_time IS NOT NULL;

CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    parent_id UUID NULL REFERENCES parents(id) ON DELETE SET NULL,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    payment_method payment_method NOT NULL DEFAULT 'cash',
    kaspi_notification_id UUID NULL UNIQUE REFERENCES incoming_notifications(id) ON DELETE SET NULL,
    comment TEXT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_payments_student_id ON payments(student_id);
CREATE INDEX ix_payments_recorded_at ON payments(recorded_at);

CREATE TABLE expenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category expense_category NOT NULL,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    description TEXT NULL,
    payment_method expense_payment_method NOT NULL DEFAULT 'cash',
    expense_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_expenses_expense_date ON expenses(expense_date);
CREATE INDEX ix_expenses_category ON expenses(category);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_students_updated_at ON students;
CREATE TRIGGER trg_students_updated_at
BEFORE UPDATE ON students
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_events_updated_at ON events;
CREATE TRIGGER trg_events_updated_at
BEFORE UPDATE ON events
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_lessons_updated_at ON lessons;
CREATE TRIGGER trg_lessons_updated_at
BEFORE UPDATE ON lessons
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
