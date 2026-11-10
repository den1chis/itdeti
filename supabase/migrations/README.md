# Миграции базы (`supabase/migrations/`)

> ## ⚠️ `001_reset_school_schema.sql` на живой базе выполнять НЕЛЬЗЯ
> Этот файл удаляет (`DROP TABLE … CASCADE`) и заново создаёт все таблицы школы: `students`, `lessons`,
> `payments`, `expenses`, `student_schedule`, `events`, `parents`, `student_parents` и др. **Все строки в них
> будут потеряны.** Он один раз создал схему текущей версии и хранится только как история.

## Как применять
- Вручную, в SQL Editor проекта Supabase, **по порядку номеров**, только те, что ещё не применены к этой базе.
- Перед применением к проду — бэкап (см. `docs/DEPLOY_RUNBOOK.md`) и `scripts/prod_preflight.sql`.
- К dev-базе: `python scripts/apply_migration.py <номер>` (скрипт работает **только** с dev-базой
  `itdeti-dev`, отказывается от других, проверяет «только добавления» и RLS у новых таблиц; `--check` — проверка без применения).
- Новые миграции: только добавления (без `DROP`, `RENAME`, смены типа колонки, `DELETE`, `TRUNCATE`),
  каждая новая таблица — с `ALTER TABLE … ENABLE ROW LEVEL SECURITY`, идемпотентно (`IF NOT EXISTS`).
- Содержимое уже применённых файлов не меняется.

## Список
| № | Файл | Что делает | Запускать повторно? |
|---|---|---|---|
| 001 | `001_reset_school_schema.sql` | **Пересоздаёт** все таблицы школы (сохраняет только `users`, `refresh_tokens`, `incoming_notifications`) | ❌ **Никогда на живой базе** — потеря данных |
| 002 | `002_hardening.sql` | Удаляет дубли слотов расписания и уроков, уникальные индексы, `payments.request_id` | нет: меняет данные (удаляет строки) |
| 003 | `003_ui_logic.sql` | `students.course`, необязательный телефон родителя, `lesson_kind` | нет нужды |
| 004 | `004_add_masterclass_event_type.sql` | значение `masterclass` в `event_type` | нет нужды |
| 005 | `005_finance_hardening.sql` | категории расходов, отмена операций, повторяющиеся расходы | нет нужды |
| 006 | `006_recurring_events_and_manual_lessons.sql` | `recurring_events`, защита от дублей уроков | нет нужды |
| 007 | `007_manual_recurring_finance.sql` | повторяющиеся доходы и их записи | нет нужды |
| 008 | `008_lms_catalog.sql` | таблицы `lms_*` (каталог курсов, назначения, прогресс, заметки) | нет нужды (`IF NOT EXISTS`) |
| 009 | — | удалена (не нужна) | — |
| 010 | `010_clear_student_course_text.sql` | одноразово обнуляет `students.course` | ❌ не запускать повторно: сотрёт названия назначенных курсов |
| 011 | `011_tasks.sql` | задачи: `task_projects`, `tasks`, `task_occurrences` | нет нужды (`IF NOT EXISTS`) |
| 012 | `012_student_accounts.sql` | `student_accounts` (с RLS), `students.teacher_id`, значение `methodist` в `user_role` (ночь 1; в комментарии файла остался старый путь `migrations/001_…`) | можно: идемпотентна (проверено на dev) |
| 013 | `013_access_model.sql` | `users.roles` (+ заполнение из `users.role`), `lessons.teacher_id`, индексы, `student_accounts.password_enc` (ночь 2). Требует 012 | можно: идемпотентна (проверено на dev) |

Файлы 008 и 011 не включают RLS для своих таблиц: на проде проверить `scripts/prod_preflight.sql`
(раздел «RLS») и при необходимости включить отдельной миграцией.
