BEGIN;

ALTER TABLE students
    ADD COLUMN IF NOT EXISTS course TEXT NULL;

ALTER TABLE parents
    ALTER COLUMN phone DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'lesson_kind'
    ) THEN
        CREATE TYPE lesson_kind AS ENUM ('lesson', 'masterclass');
    END IF;
END $$;

ALTER TABLE lessons
    ADD COLUMN IF NOT EXISTS lesson_kind lesson_kind;

UPDATE lessons
SET lesson_kind = 'lesson'
WHERE lesson_kind IS NULL;

ALTER TABLE lessons
    ALTER COLUMN lesson_kind SET DEFAULT 'lesson';

ALTER TABLE lessons
    ALTER COLUMN lesson_kind SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'schedule_lesson_kind'
    ) THEN
        CREATE TYPE schedule_lesson_kind AS ENUM ('lesson', 'masterclass');
    END IF;
END $$;

ALTER TABLE student_schedule
    ADD COLUMN IF NOT EXISTS lesson_kind schedule_lesson_kind;

UPDATE student_schedule
SET lesson_kind = 'lesson'
WHERE lesson_kind IS NULL;

ALTER TABLE student_schedule
    ALTER COLUMN lesson_kind SET DEFAULT 'lesson';

ALTER TABLE student_schedule
    ALTER COLUMN lesson_kind SET NOT NULL;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS request_id VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_request_id
    ON payments(request_id)
    WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_lessons_status
    ON lessons(status);

COMMIT;
