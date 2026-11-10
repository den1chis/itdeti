-- prod_backup.sql — выгрузка всех таблиц схемы public в JSON (бэкап перед выкладкой).
-- ТОЛЬКО ЧТЕНИЕ: оба шага — SELECT. Supabase → прод-проект → SQL Editor. Подробно: docs/DEPLOY_RUNBOOK.md, шаг 1.
--
-- Шаг А. Выполнить запрос ниже. Он ничего не выгружает, а ПИШЕТ текст второго запроса (одна ячейка).
--        Скопировать содержимое ячейки целиком (двойной клик по ячейке → выделить всё → копировать).
-- Шаг Б. Открыть новую вкладку SQL Editor, вставить скопированный текст, Run.
--        Результат: по строке на таблицу — table_name, rows (число строк), data (все строки таблицы в JSON).
--        Сохранить: кнопка «Export» / «Download CSV» над результатом → файл
--        itdeti_backup_ГГГГ-ММ-ДД.csv. Хранить в надёжном месте (не в чате, не в публичном репозитории):
--        в файле персональные данные учеников и родителей, хеши паролей и refresh-токены.
-- Проверка: число строк в rows для students, lessons, payments совпадает с prod_preflight.sql, запрос [11].

SELECT string_agg(
         format('SELECT %L AS table_name, count(*) AS rows, coalesce(json_agg(t), ''[]''::json)::text AS data FROM public.%I t',
                tablename, tablename),
         E'\nUNION ALL\n' ORDER BY tablename
       ) || E'\nORDER BY 1;' AS backup_query
FROM pg_tables
WHERE schemaname = 'public';
