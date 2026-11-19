-- prod_postcheck_016.sql — проверка прод-базы ПОСЛЕ миграции 016_savings.sql (ночь 6, «Накопления»).
-- ТОЛЬКО ЧТЕНИЕ. Запуск: python scripts/prod_ops.py --prod postcheck 016
-- Формат и правила — как в prod_preflight.sql. Не норма — СТОП, код не выкладывать
-- (старый код новые таблицы не замечает: миграция только добавляет).
-- Совпадение counts и контрольных сумм старых финансовых таблиц проверяет отдельно
-- `prod_ops.py --prod finance --compare` (шаг 3 deploy_night6.py).

-- [1] Новые таблицы на месте.
-- @expect all_true
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'savings_accounts')  AS savings_accounts,
       EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'savings_movements') AS savings_movements;

-- [2] RLS у обеих новых таблиц включён.
-- @expect scalar == 2
SELECT count(*) AS with_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
  AND c.relname IN ('savings_accounts', 'savings_movements');

-- [3] Колонки, которые читает и пишет новый код.
-- @expect scalar == 18
SELECT count(*) AS columns
FROM information_schema.columns
WHERE table_schema = 'public'
  AND ((table_name = 'savings_accounts' AND column_name IN ('id', 'name', 'kind', 'is_archived', 'archived_at', 'created_at', 'updated_at'))
    OR (table_name = 'savings_movements' AND column_name IN ('id', 'account_id', 'kind', 'amount', 'movement_date', 'comment',
                                                             'created_by', 'created_at', 'updated_at', 'is_cancelled', 'cancelled_at')));

-- [4] Суммы — целые тенге (bigint).
-- @expect scalar == 1
SELECT count(*) AS amount_bigint
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'savings_movements' AND column_name = 'amount' AND data_type = 'bigint';

-- [5] Индексы 016 созданы и действительны.
-- @expect scalar == 2
SELECT count(*) AS valid_indexes
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND i.indisvalid
  AND c.relname IN ('idx_savings_movements_account_id', 'idx_savings_movements_movement_date');

-- [6] Счёт с движениями не удаляется каскадом (RESTRICT), автор движения — SET NULL.
-- @expect all_true
SELECT EXISTS (SELECT 1 FROM information_schema.referential_constraints rc
               JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
               JOIN information_schema.constraint_column_usage u ON u.constraint_name = rc.unique_constraint_name AND u.constraint_schema = rc.unique_constraint_schema
               WHERE tc.table_schema = 'public' AND tc.table_name = 'savings_movements' AND u.table_name = 'savings_accounts'
                 AND rc.delete_rule = 'RESTRICT') AS account_restrict,
       EXISTS (SELECT 1 FROM information_schema.referential_constraints rc
               JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
               JOIN information_schema.constraint_column_usage u ON u.constraint_name = rc.unique_constraint_name AND u.constraint_schema = rc.unique_constraint_schema
               WHERE tc.table_schema = 'public' AND tc.table_name = 'savings_movements' AND u.table_name = 'users'
                 AND rc.delete_rule = 'SET NULL') AS author_set_null;

-- [7] Новые таблицы пустые (счета владелец заведёт сам).
-- @expect scalar == 0
SELECT (SELECT count(*) FROM public.savings_accounts) + (SELECT count(*) FROM public.savings_movements) AS rows_in_new_tables;

-- [8] Старые финансовые таблицы по-прежнему с RLS (миграция их не трогала).
-- @expect info
SELECT c.relname AS finance_table, c.relrowsecurity AS rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND c.relname IN ('payments', 'expenses', 'recurring_incomes', 'recurring_expenses', 'recurring_income_records')
ORDER BY c.relname;
