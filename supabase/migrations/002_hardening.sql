BEGIN;

-- Remove duplicate weekly slots created by repeated form submissions.
WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY student_id, day_of_week, start_time, valid_from
            ORDER BY created_at, id
        ) AS rn
    FROM student_schedule
), duplicates AS (
    SELECT id
    FROM ranked
    WHERE rn > 1
)
DELETE FROM student_schedule s
USING duplicates d
WHERE s.id = d.id;

-- Remove duplicate generated lesson occurrences, keeping the earliest one.
WITH ranked AS (
    SELECT
        id,
        event_id,
        ROW_NUMBER() OVER (
            PARTITION BY schedule_slot_id, original_start_time
            ORDER BY created_at, id
        ) AS rn
    FROM lessons
    WHERE schedule_slot_id IS NOT NULL
      AND original_start_time IS NOT NULL
), duplicates AS (
    SELECT id, event_id
    FROM ranked
    WHERE rn > 1
), deleted_lessons AS (
    DELETE FROM lessons l
    USING duplicates d
    WHERE l.id = d.id
    RETURNING l.event_id
)
DELETE FROM events e
USING deleted_lessons d
WHERE e.id = d.event_id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_student_schedule_slot_identity
    ON student_schedule(student_id, day_of_week, start_time, valid_from);

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS request_id VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_request_id
    ON payments(request_id)
    WHERE request_id IS NOT NULL;

COMMIT;
