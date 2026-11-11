-- prod_postcheck.sql — проверка прод-базы ПОСЛЕ миграций 012_student_accounts.sql и 013_access_model.sql.
-- ТОЛЬКО ЧТЕНИЕ: одни SELECT. Supabase → прод-проект → SQL Editor, выполнять по одному запросу (Ctrl+Enter).
-- Всё должно совпасть с «Норма:»; иначе — СТОП, код не выкладывать (docs/DEPLOY_RUNBOOK.md, шаг 3).

-- [1] Новые колонки и таблица на месте.
-- Норма: все пять значений true.
SELECT
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'roles')            AS users_roles,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'students' AND column_name = 'teacher_id')     AS students_teacher_id,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'lessons' AND column_name = 'teacher_id')      AS lessons_teacher_id,
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'student_accounts')                             AS student_accounts,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'student_accounts' AND column_name = 'password_enc') AS student_accounts_password_enc;

-- [2] В user_role появилось значение methodist.
-- Норма: true.
SELECT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
    WHERE t.typname = 'user_role' AND e.enumlabel = 'methodist'
) AS methodist_in_user_role;

-- [3] users.roles заполнено у всех, у кого есть role.
-- Норма: 0 (ноль пользователей с пустым набором ролей при заполненном role).
SELECT count(*) AS users_with_empty_roles
FROM users
WHERE role IS NOT NULL AND cardinality(roles) = 0;

-- [4] Набор ролей совпадает со старой ролью (для наглядности).
-- Норма: у каждой строки roles = {role}, например admin -> {admin}.
SELECT role, roles, count(*) AS users
FROM users
GROUP BY role, roles
ORDER BY role;

-- [5] RLS на student_accounts включён.
-- Норма: true.
SELECT relrowsecurity AS student_accounts_rls
FROM pg_class
WHERE oid = 'public.student_accounts'::regclass;

-- [6] Внешние ключи и индексы новых колонок.
-- Норма: 3 строки внешних ключей: lessons.teacher_id -> users (SET NULL), students.teacher_id -> users (SET NULL),
--        student_accounts.student_id -> students (CASCADE).
SELECT
    c.conrelid::regclass AS table_name,
    a.attname            AS column_name,
    c.confrelid::regclass AS references_table,
    CASE c.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'a' THEN 'NO ACTION' ELSE c.confdeltype::text END AS on_delete
FROM pg_constraint c
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
WHERE c.contype = 'f'
  AND ((c.conrelid = 'public.lessons'::regclass AND a.attname = 'teacher_id')
    OR (c.conrelid = 'public.students'::regclass AND a.attname = 'teacher_id')
    OR (c.conrelid = 'public.student_accounts'::regclass AND a.attname = 'student_id'))
ORDER BY 1, 2;

-- Норма: idx_lessons_teacher_id и idx_students_teacher_id есть.
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public' AND indexname IN ('idx_lessons_teacher_id', 'idx_students_teacher_id')
ORDER BY 1;

-- [7] Новые колонки пустые — данные школы не тронуты.
-- Норма: students_with_teacher = 0, lessons_with_teacher = 0, student_accounts = 0 (сразу после миграций).
SELECT
    (SELECT count(*) FROM students WHERE teacher_id IS NOT NULL) AS students_with_teacher,
    (SELECT count(*) FROM lessons  WHERE teacher_id IS NOT NULL) AS lessons_with_teacher,
    (SELECT count(*) FROM student_accounts)                      AS student_accounts;

-- [8] Число строк в основных таблицах — сравнить с запросом [11] из prod_preflight.sql.
-- Норма: те же числа (миграции строк не удаляют).
SELECT 'users' AS table_name, count(*) AS rows FROM users
UNION ALL SELECT 'students', count(*) FROM students
UNION ALL SELECT 'parents', count(*) FROM parents
UNION ALL SELECT 'lessons', count(*) FROM lessons
UNION ALL SELECT 'events', count(*) FROM events
UNION ALL SELECT 'payments', count(*) FROM payments
UNION ALL SELECT 'expenses', count(*) FROM expenses
UNION ALL SELECT 'student_schedule', count(*) FROM student_schedule
UNION ALL SELECT 'refresh_tokens', count(*) FROM refresh_tokens
UNION ALL SELECT 'lms_courses', count(*) FROM lms_courses
UNION ALL SELECT 'lms_enrollments', count(*) FROM lms_enrollments
UNION ALL SELECT 'tasks', count(*) FROM tasks
ORDER BY 1;
