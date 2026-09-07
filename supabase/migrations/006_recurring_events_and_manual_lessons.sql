-- ITdeti: recurring calendar events + safe historical lesson deduplication
-- Additive migration. Existing rows are preserved.

CREATE TABLE IF NOT EXISTS recurring_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'personal',
    frequency TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly')),
    interval INTEGER NOT NULL DEFAULT 1 CHECK (interval >= 1 AND interval <= 52),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    start_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes >= 15 AND duration_minutes <= 480),
    location TEXT,
    notes TEXT,
    color VARCHAR(20) NOT NULL DEFAULT '#64748b',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT recurring_events_period_check CHECK (end_date >= start_date)
);

ALTER TABLE events
    ADD COLUMN IF NOT EXISTS recurring_event_id UUID
    REFERENCES recurring_events(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_events_recurring_event_id
    ON events(recurring_event_id);

-- Do not add a unique index: an existing database may already contain
-- historical duplicates. Instead, prevent NEW duplicate lessons only.
-- This covers both a manual historical lesson and a lesson later generated
-- from a schedule at the same student/date/time.
CREATE OR REPLACE FUNCTION prevent_duplicate_lesson_occurrence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.original_start_time IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM lessons l
            WHERE l.student_id = NEW.student_id
              AND l.original_start_time = NEW.original_start_time
              AND l.id <> COALESCE(NEW.id, gen_random_uuid())
        ) THEN
            RAISE EXCEPTION 'duplicate lesson occurrence for student and start time';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_prevent_duplicate_lesson_occurrence ON lessons;
CREATE TRIGGER trg_prevent_duplicate_lesson_occurrence
BEFORE INSERT ON lessons
FOR EACH ROW
EXECUTE FUNCTION prevent_duplicate_lesson_occurrence();

DROP TRIGGER IF EXISTS trg_recurring_events_updated_at ON recurring_events;
CREATE TRIGGER trg_recurring_events_updated_at
BEFORE UPDATE ON recurring_events
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
