-- prod_preflight.sql — проверка прод-базы ДО миграций 012 и 013.
-- ТОЛЬКО ЧТЕНИЕ: в файле одни SELECT, ничего не меняет. Выполнять в Supabase → прод-проект → SQL Editor.
-- Как пользоваться: вставить весь файл и нажать Run — SQL Editor покажет результат ПОСЛЕДНЕГО запроса;
-- поэтому удобнее выделять по одному запросу (блок между строками «-- [N]») и нажимать Run (Ctrl+Enter).
-- Результат каждого запроса сравнить с «Норма:» в комментарии. Разбор — docs/DEPLOY_RUNBOOK.md, шаг 2.

-- [1] Сколько таблиц в схеме public.
-- Норма: около 30 (школа + lms_* + задачи + старые служебные таблицы). На dev-базе сейчас 33 таблицы,
--        из них student_accounts добавлена миграцией 012, то есть до миграций там было 32.
--        Резкое отличие (например, 0 или 5) — СТОП, открыт не тот проект.
SELECT count(*) AS tables_in_public
FROM pg_tables
WHERE schemaname = 'public';

-- [2] Список таблиц и включён ли RLS.
-- Норма: у всех rls_enabled = true. Если у какой-то false — таблица открыта ролям anon/authenticated
--        через PostgREST (API Supabase). Записать в отчёт; это не мешает выкладке, но чинится отдельной миграцией.
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relrowsecurity, c.relname;

-- [3] Только таблицы БЕЗ RLS.
-- Норма: 0 строк.
SELECT c.relname AS table_without_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
ORDER BY c.relname;

-- [4] Значения перечисления user_role.
-- Норма ДО миграций: admin, teacher, parent, student (порядок может отличаться); 'methodist' ещё нет.
--        Если 'methodist' уже есть — 012 когда-то частично применялась; это не страшно (миграции идемпотентны).
SELECT e.enumlabel AS user_role_value
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typname = 'user_role'
ORDER BY e.enumsortorder;

-- [5] Что из миграций 012–013 уже есть.
-- Норма ДО миграций: все четыре значения false.
--        Если что-то true — миграции безопасно применить всё равно (IF NOT EXISTS), но отметить в отчёте.
SELECT
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'roles')        AS users_roles_exists,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'students' AND column_name = 'teacher_id') AS students_teacher_id_exists,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'lessons' AND column_name = 'teacher_id')  AS lessons_teacher_id_exists,
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'student_accounts')                         AS student_accounts_exists;

-- [6] Таблицы, которые миграции 012–013 изменяют, должны существовать.
-- Норма: 3 строки — lessons, students, users. Меньше — СТОП (миграция упадёт).
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN ('users', 'students', 'lessons')
ORDER BY table_name;

-- [7] Структура lms_enrollments (сверка с 008_lms_catalog.sql).
-- Норма (по 008): id uuid, student_id uuid, course_id uuid, status text, started_at timestamptz — 5 колонок.
--        Лишние start_date, weekday, lesson_time — остаток удалённой миграции 009 (на dev они есть);
--        не мешают, просто отметить.
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'lms_enrollments'
ORDER BY ordinal_position;

-- [8] Ограничения lms_enrollments: есть ли UNIQUE (student_id, course_id) из 008.
-- Норма: строки с типом p (первичный ключ), f (2 внешних ключа) и u (uq_lms_enrollments_student_course).
--        Нет строки u — расхождение с репозиторием (на dev её тоже нет); API всё равно не даёт второй курс (409).
SELECT conname AS constraint_name, contype AS type, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.lms_enrollments'::regclass
ORDER BY contype, conname;

-- [9] Внешние ключи всех lms_* и их правило удаления (сверка с 008).
-- Норма (по 008): ON DELETE CASCADE у всех, кроме lms_student_notes.lesson_id (SET NULL).
--        В выводе это on_delete = 'CASCADE' / 'SET NULL'. 'NO ACTION' — расхождение с репозиторием
--        (так на dev): удаление курса/урока/ученика с LMS-данными упадёт с ошибкой вместо каскада. Отметить.
SELECT
    c.conrelid::regclass AS table_name,
    a.attname            AS column_name,
    c.confrelid::regclass AS references_table,
    CASE c.confdeltype
        WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT' WHEN 'd' THEN 'SET DEFAULT' END AS on_delete
FROM pg_constraint c
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE 'lms\_%'
ORDER BY 1, 2;

-- [10] Индексы lms_* (сверка с 008: ix_lms_modules_course_id, ix_lms_lessons_module_id, ix_lms_materials_lesson_id,
--      ix_lms_tasks_lesson_id, ix_lms_enrollments_student_id, ix_lms_enrollments_course_id,
--      ix_lms_lesson_progress_lesson_id, ix_lms_student_notes_student_id).
-- Норма: все 8 перечисленных есть (плюс первичные ключи *_pkey). Отсутствие — только медленнее, не ошибка.
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public' AND tablename LIKE 'lms\_%'
ORDER BY tablename, indexname;

-- [11] Число строк в основных таблицах (для сравнения после выкладки — должно совпасть).
-- Норма: реальные числа школы; записать их. После выкладки повторить — числа не должны уменьшиться.
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

-- [12] Пользователи CRM: роли и активность (без паролей и email).
-- Норма: 1 активный admin (Денис Александрович) и, возможно, служебные записи; is_active = NULL встречаться не должен
--        (такой пользователь не сможет войти).
SELECT role, is_active, count(*) AS users
FROM users
GROUP BY role, is_active
ORDER BY role, is_active;
