-- prod_preflight.sql — проверка прод-базы ПЕРЕД выкладкой ночи 5 (миграции до 014 применены, 015 — следующая).
-- ТОЛЬКО ЧТЕНИЕ: в файле одни SELECT. Запуск: python scripts/prod_ops.py --prod preflight
-- (можно и руками в Supabase → SQL Editor, по одному запросу).
-- Формат для prod_ops.py: блок «-- [N] название», строка «-- @expect ПРАВИЛО», один SELECT.
-- Правила: info (только показать) | all_true | rows == N | rows >= N | scalar == V | scalar >= N.
-- В запросах нет персональных данных: только числа, имена таблиц и признаки.
-- (Версия ночи 4 — проверка перед 014 — в истории git: коммит 82e7301.)

-- [1] Таблиц в схеме public (0 или единицы — открыт не тот проект).
-- @expect scalar >= 25
SELECT count(*) AS tables_in_public
FROM pg_tables
WHERE schemaname = 'public';

-- [2] Таблицы без RLS (не мешает выкладке; записать в отчёт).
-- @expect info
SELECT c.relname AS table_without_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
ORDER BY c.relname;

-- [3] Миграции 012–014 применены (новый код читает эти таблицы и колонки).
-- @expect all_true
SELECT
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'roles')                  AS users_roles,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'students' AND column_name = 'teacher_id')           AS students_teacher_id,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'lessons' AND column_name = 'teacher_id')            AS lessons_teacher_id,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'student_accounts' AND column_name = 'password_enc') AS password_enc,
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'audit_log')                                        AS audit_log,
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'auth_login_state')                                 AS auth_login_state,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'refresh_tokens' AND column_name = 'token_hash')    AS refresh_tokens_token_hash;

-- [4] public.users.id — uuid (на него ссылается новая таблица 015).
-- @expect scalar == 1
SELECT count(*) AS users_id_uuid
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'id' AND data_type = 'uuid';

-- [5] Пользователи, чьи email отличаются только регистром или пробелами (вход без учёта регистра).
-- @expect scalar == 0
SELECT count(*) AS emails_differing_only_by_case
FROM (SELECT lower(trim(email)) FROM users GROUP BY 1 HAVING count(*) > 1) duplicates;

-- [6] Активных администраторов (должен быть хотя бы один).
-- @expect scalar >= 1
SELECT count(*) AS active_admins
FROM users
WHERE is_active IS TRUE AND (role = 'admin' OR 'admin' = ANY (roles));

-- [7] Пользователи по ролям и активности (без email).
-- @expect info
SELECT role, is_active, count(*) AS users
FROM users
GROUP BY role, is_active
ORDER BY role, is_active;

-- [8] Ученики с преподавателем (до ночи 5 преподавателей на проде нет — ожидается 0).
-- @expect info
SELECT count(*) FILTER (WHERE teacher_id IS NOT NULL) AS with_teacher, count(*) AS students
FROM students;

-- [9] Сервер умеет gen_random_uuid() без расширений (PostgreSQL 13+).
-- @expect scalar >= 130000
SELECT current_setting('server_version_num')::int AS server_version_num;

-- [10] Что из 015 уже есть (до миграции — false; true не мешает: миграция идемпотентна).
-- @expect info
SELECT EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'auth_handoff_codes') AS auth_handoff_codes;
