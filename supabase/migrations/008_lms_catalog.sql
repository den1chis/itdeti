-- LMS catalog: courses -> modules -> lessons -> materials/tasks, student enrollments, progress, notes.
-- Purely additive: creates NEW tables prefixed with lms_. Touches no existing table, type, function or data.
-- Safe to run more than once (IF NOT EXISTS everywhere).
-- Materials are links (Google Drive); no file storage is used.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS lms_courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    course_type TEXT NOT NULL DEFAULT 'code' CHECK (course_type IN ('code', 'no_code')),
    description TEXT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lms_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lms_modules_course_id ON lms_modules(course_id);

CREATE TABLE IF NOT EXISTS lms_lessons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id UUID NOT NULL REFERENCES lms_modules(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NULL,
    duration_min INTEGER NULL CHECK (duration_min IS NULL OR duration_min > 0),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lms_lessons_module_id ON lms_lessons(module_id);

CREATE TABLE IF NOT EXISTS lms_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'link' CHECK (kind IN ('link', 'text')),
    title TEXT NOT NULL,
    url TEXT NULL,
    body TEXT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_lms_materials_content CHECK (
        (kind = 'link' AND url IS NOT NULL) OR (kind = 'text' AND body IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_lms_materials_lesson_id ON lms_materials(lesson_id);

CREATE TABLE IF NOT EXISTS lms_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'practice' CHECK (kind IN ('code', 'practice')),
    title TEXT NOT NULL,
    statement TEXT NULL,
    starter_code TEXT NULL,
    tests JSONB NULL,
    hint TEXT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lms_tasks_lesson_id ON lms_tasks(lesson_id);

CREATE TABLE IF NOT EXISTS lms_enrollments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'finished')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_lms_enrollments_student_course UNIQUE (student_id, course_id)
);

CREATE INDEX IF NOT EXISTS ix_lms_enrollments_student_id ON lms_enrollments(student_id);
CREATE INDEX IF NOT EXISTS ix_lms_enrollments_course_id ON lms_enrollments(course_id);

CREATE TABLE IF NOT EXISTS lms_lesson_progress (
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    lesson_id UUID NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'done')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (student_id, lesson_id)
);

CREATE INDEX IF NOT EXISTS ix_lms_lesson_progress_lesson_id ON lms_lesson_progress(lesson_id);

CREATE TABLE IF NOT EXISTS lms_student_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    lesson_id UUID NULL REFERENCES lms_lessons(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lms_student_notes_student_id ON lms_student_notes(student_id);

COMMIT;
