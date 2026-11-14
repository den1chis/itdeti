-- prod_postcheck_015.sql — проверка прод-базы ПОСЛЕ миграции 015_staff_handoff.sql.
-- ТОЛЬКО ЧТЕНИЕ. Запуск: python scripts/prod_ops.py --prod postcheck 015
-- Формат и правила — как в prod_preflight.sql. Не норма — СТОП, код не выкладывать
-- (старый код новую таблицу не замечает: миграция только добавляет).

-- [1] Новая таблица на месте.
-- @expect all_true
SELECT EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'auth_handoff_codes') AS auth_handoff_codes;

-- [2] RLS у новой таблицы включён.
-- @expect scalar == 1
SELECT count(*) AS with_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity AND c.relname = 'auth_handoff_codes';

-- [3] Колонки, которые читает и пишет новый код.
-- @expect scalar == 7
SELECT count(*) AS columns
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'auth_handoff_codes'
  AND column_name IN ('id', 'code_hash', 'user_id', 'target', 'expires_at', 'created_at', 'created_ip');

-- [4] Индексы 015 созданы и действительны (уникальный по хешу — основа одноразовости).
-- @expect scalar == 3
SELECT count(*) AS valid_indexes
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND i.indisvalid
  AND c.relname IN ('uq_auth_handoff_codes_code_hash', 'idx_auth_handoff_codes_expires_at', 'idx_auth_handoff_codes_user_id');

-- [5] Внешний ключ на users с удалением каскадом.
-- @expect scalar == 1
SELECT count(*) AS fk_users
FROM information_schema.referential_constraints rc
JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
WHERE tc.table_schema = 'public' AND tc.table_name = 'auth_handoff_codes' AND rc.delete_rule = 'CASCADE';

-- [6] Таблица пустая (кодов ещё никто не выдавал).
-- @expect scalar == 0
SELECT count(*) AS codes FROM public.auth_handoff_codes;
