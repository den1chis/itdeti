-- ITdeti: recurring calendar events + safe manual historical lessons
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

-- A manually entered historical lesson has no schedule slot but does have
-- original_start_time. Prevent accidental duplicate manual lessons for the
-- same student/date/time without affecting existing recurring lessons.
CREATE UNIQUE INDEX IF NOT EXISTS uq_manual_lesson_student_time
    ON lessons(student_id, original_start_time)
    WHERE schedule_slot_id IS NULL AND original_start_time IS NOT NULL;

-- Keep updated_at correct for recurring event records.
DROP TRIGGER IF EXISTS trg_recurring_events_updated_at ON recurring_events;
CREATE TRIGGER trg_recurring_events_updated_at
BEFORE UPDATE ON recurring_events
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
