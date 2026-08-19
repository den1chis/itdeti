BEGIN;

-- Prevent accidental duplicate clicks from creating identical weekly slots.
CREATE UNIQUE INDEX IF NOT EXISTS uq_student_schedule_slot_identity
    ON student_schedule(student_id, day_of_week, start_time, valid_from);

-- Make manual payment creation idempotent when the frontend retries the same request.
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS request_id VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_request_id
    ON payments(request_id)
    WHERE request_id IS NOT NULL;

COMMIT;
