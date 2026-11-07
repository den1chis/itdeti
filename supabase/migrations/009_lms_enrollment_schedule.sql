-- LMS: расписание назначения курса (подготовка к автоматической расстановке уроков в календаре).
-- Только добавляет три необязательные колонки в lms_enrollments. Существующие данные не меняются.
-- Безопасно запускать повторно.

BEGIN;

ALTER TABLE lms_enrollments ADD COLUMN IF NOT EXISTS start_date DATE NULL;
ALTER TABLE lms_enrollments ADD COLUMN IF NOT EXISTS weekday SMALLINT NULL;
ALTER TABLE lms_enrollments ADD COLUMN IF NOT EXISTS lesson_time TIME NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_lms_enrollments_weekday'
    ) THEN
        ALTER TABLE lms_enrollments
            ADD CONSTRAINT ck_lms_enrollments_weekday CHECK (weekday IS NULL OR (weekday >= 0 AND weekday <= 6));
    END IF;
END $$;

COMMIT;
