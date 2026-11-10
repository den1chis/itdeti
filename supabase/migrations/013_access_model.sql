-- 013_access_model.sql — access model (night 2): several roles per user, teacher of a lesson,
-- viewable (encrypted) student passwords.
-- Additive only: new columns and indexes. Nothing is dropped, renamed or retyped; users.role stays.
-- Safe to run more than once (IF NOT EXISTS / filling only empty values).
-- Requires 012_student_accounts.sql (student_accounts, students.teacher_id, user_role 'methodist').
-- Deploy order: this migration first, then the code that reads these columns.
--   python scripts/apply_migration.py 013

BEGIN;

-- 1. A set of roles per user (a methodist can also be a teacher). The old single users.role is kept
--    and stays in sync for the existing CRM/Android code; the server reads users.roles.
ALTER TABLE users ADD COLUMN IF NOT EXISTS roles user_role[] NOT NULL DEFAULT '{}';
UPDATE users
   SET roles = ARRAY[role]::user_role[]
 WHERE cardinality(roles) = 0
   AND role IS NOT NULL;

-- 2. Teacher of one lesson: a substitute for a future lesson, or the actual teacher of a held lesson
--    (filled when the lesson becomes "completed"; NULL = the student's permanent teacher).
ALTER TABLE lessons ADD COLUMN IF NOT EXISTS teacher_id UUID NULL REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_lessons_teacher_id ON lessons(teacher_id);
CREATE INDEX IF NOT EXISTS idx_students_teacher_id ON students(teacher_id);

-- 3. Student password encrypted with a server-side key (STUDENT_PASSWORD_KEY, Fernet) so that the
--    teacher/admin can look it up later. The bcrypt hash in password_hash is still what login checks.
ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS password_enc TEXT NULL;

COMMIT;
