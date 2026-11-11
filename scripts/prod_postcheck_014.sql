-- prod_postcheck_014.sql — проверка прод-базы ПОСЛЕ миграции 014_security.sql.
-- ТОЛЬКО ЧТЕНИЕ. Запуск: python scripts/prod_ops.py --prod postcheck 014
-- Формат и правила — как в prod_preflight.sql. Не норма — СТОП, код не выкладывать
-- (старый код новые таблицы и колонку не замечает: миграция только добавляет).

-- [1] Новые таблицы и колонка на месте.
-- @expect all_true
SELECT
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'audit_log')                                        AS audit_log,
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'auth_login_state')                                 AS auth_login_state,
    EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'auth_email_locks')                                 AS auth_email_locks,
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'refresh_tokens' AND column_name = 'token_hash')    AS refresh_tokens_token_hash;

-- [2] RLS включён у всех трёх новых таблиц.
-- @expect scalar == 3
SELECT count(*) AS new_tables_with_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
  AND c.relname IN ('audit_log', 'auth_login_state', 'auth_email_locks');

-- [3] Индексы 014 созданы и действительны.
-- @expect scalar == 5
SELECT count(*) AS valid_indexes
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND i.indisvalid
  AND c.relname IN ('idx_auth_login_state_updated_at', 'idx_audit_log_created_at', 'idx_audit_log_action_created_at',
                    'uq_refresh_tokens_token_hash', 'idx_refresh_tokens_expires_at');

-- [4] Хеш заполнен верно: где он есть у старых (открытых) записей, он равен SHA-256 токена.
-- @expect scalar == 0
SELECT count(*) AS wrong_hashes
FROM public.refresh_tokens
WHERE token_hash IS NOT NULL
  AND token NOT LIKE 'sha256:%'
  AND token_hash <> encode(sha256(convert_to(token, 'UTF8')), 'hex');

-- [5] Старая колонка token не тронута: пустых значений нет.
-- @expect scalar == 0
SELECT count(*) AS empty_tokens
FROM public.refresh_tokens
WHERE token IS NULL OR token = '';

-- [6] Refresh-токены: всего / с хешем / без хеша (без хеша — выданные старым сервером уже после миграции;
--     они работают через старую колонку и заменяются при первом использовании).
-- @expect info
SELECT count(*) AS total, count(token_hash) AS with_hash, count(*) - count(token_hash) AS without_hash
FROM public.refresh_tokens;

-- [7] Новые таблицы пустые или почти пустые (данные школы не тронуты).
-- @expect info
SELECT
    (SELECT count(*) FROM public.audit_log)        AS audit_log,
    (SELECT count(*) FROM public.auth_login_state) AS auth_login_state,
    (SELECT count(*) FROM public.auth_email_locks) AS auth_email_locks;

-- [8] Активных администраторов (как до миграции: хотя бы один).
-- @expect scalar >= 1
SELECT count(*) AS active_admins
FROM users
WHERE is_active IS TRUE AND (role = 'admin' OR 'admin' = ANY (roles));
