# REPORT — itdeti, ночь 2 (модель доступа: роли и защита на сервере)

Дата: 2026-11-10. Только бэкенд и тесты (`itdeti`, ветка `lms-mvp`). `itdetiWeb` и `itdetiLearn` не менялись. `git push`, merge и деплой не выполнялись; всё применялось только к dev-базе `itdeti-dev`. Копия отчёта: `itdeti/REPORT_NIGHT2.md`.

Коммиты ночи 2 в `itdeti/lms-mvp` (поверх `42d745f`, отчёта ночи 1): 12 (последний — этот отчёт)

- `e14b4e7` Routes inventory: every route with its current protection (docs/ROUTES_ACCESS.md)
- `4d9131d` Migrations: move night-1 migration to supabase/migrations/012; 013 access model (users.roles, lessons.teacher_id, student_accounts.password_enc); apply script by number with additive-only and RLS checks
- `70d0b98` Models: users.roles, students.teacher_id/level, lessons.teacher_id, student_accounts.password_enc
- `395330c` Access model core: roles set, require_admin/require_roles/require_staff markers, ownership in SQL; every route classified (legacy routers admin only, LMS catalog/student/notes classes)
- `5fce7e3` Student access: teacher sees own students' access, encrypted viewable password (STUDENT_PASSWORD_KEY), unlock; create/PATCH/suggestion stay admin only
- `59d95e8` Teacher read-only routes (/teacher/me, students, student card, lessons with substitutions) and admin staff management (/staff, dismissal rule, teacher and substitute assignment)
- `93d98bf` Held lesson records its actual teacher (lessons.teacher_id from students.teacher_id when empty); deduction logic untouched
- `e82cafe` seed_dev: teachers A/B, methodist, teacher+methodist, student distribution, parents, schedule, one substitution, encrypted student passwords
- `fe75dfc` Access model docs: routes table with access classes (before/after), README section; unmarked_routes() helper
- `1ce7408` scripts/demo_roles.py: table of what each role sees (real logins, read-only)
- `178d36e` Tests: access model (route x role matrix, unmarked routes, teacher isolation, no money for teachers, methodist, substitution, dismissal rule, student passwords); night-1 test: foreign student access is 404 for a teacher.

## 1. Итог в двух словах
- У каждого маршрута API теперь **явный класс доступа** (`core/access.py`), сервер проверяет **роль и принадлежность**. Неразмеченный маршрут валит тест.
- Все прежние роутеры (ученики, родители, финансы, расписание, события, уведомления, задачи и т. д.) — **только admin**. Для admin маршруты, ответы и статусы прежние.
- Преподаватель: только чтение своих данных через новые `/teacher/*` (без денег и личного), каталог LMS на чтение, заметки и доступы **своих** учеников (просмотр пароля, сброс, снятие блокировки). Чужое → 404.
- Методист: каталог LMS (чтение и правка), учеников и CRM не видит. Роли суммируются.
- Админ: `/staff` (сотрудники, роли, увольнение с проверкой), назначение преподавателя ученику и замены на урок.
- Тесты: **все проходят**: 21 новый (`tests/test_access_model.py`) + 57 старых (57 тестов ночи 1, один изменён намеренно, см. раздел 2.9).

## 2. Что сделано (по задачам ТЗ)

### 0. Подготовка и инвентаризация — сделано
- `git status` чистый в `itdeti`, `itdetiWeb`, `itdetiLearn`; `.env` указывает на dev (`postgres.yctyhgbhqsbawgjaiock`, `itdeti-dev`).
- `scripts/routes_inventory.py` строит таблицу всех маршрутов по дереву зависимостей FastAPI. Снимок «до» закоммичен первым (`docs/ROUTES_ACCESS.md`, 126 строк метод+путь), итоговая таблица с колонками «было / стало» — в том же файле и в разделе 4 ниже.
- Из инвентаризации: до ночи 2 **ни один** маршрут не требовал роли `admin` (кроме доступов учеников из ночи 1): GET-маршруты проверяли только вход (`get_current_user`), запись — `admin` **или** `teacher`.

### 1. Миграции — сделано, применены к dev
| Файл | Что делает |
|---|---|
| `supabase/migrations/012_student_accounts.sql` | перенос `migrations/001_student_accounts.sql` через `git mv`, содержимое не менялось (папка `migrations/` больше не нужна) |
| `supabase/migrations/013_access_model.sql` | `users.roles user_role[] NOT NULL DEFAULT '{}'` + заполнение из `users.role`; `lessons.teacher_id` (nullable, FK `users` `ON DELETE SET NULL`) + индекс; индекс `students.teacher_id` (`IF NOT EXISTS`, уже был из 012); `student_accounts.password_enc text NULL`. Только добавления, идемпотентно |

- `scripts/apply_migration.py`: принимает номер (`013`) или путь, только файлы из `supabase/migrations/`, защита «только dev» (`scripts/devguard.py`), **статическая проверка «только добавления»**: отказ при `DROP …`, `RENAME`, `ALTER COLUMN … TYPE`, `TRUNCATE`, `DELETE FROM` и при `CREATE TABLE` без `ENABLE ROW LEVEL SECURITY`; режим `--check` без применения.
- 012 и 013 применены к dev **дважды подряд** — второй запуск ничего не меняет и не падает. Dev-база после этого работает (старые тесты проходят).
- Модели: `User.roles` (массив enum `user_role`), `User.role` остаётся; `Student.teacher_id` и `Student.level` (колонка `level` есть в базе с миграции 001, в модели её не было); `Lesson.teacher_id`; `StudentAccount.password_enc`. В схемы ответов admin эти поля **не добавлены**.

### 2. Ядро доступа `core/access.py` — сделано
- `roles_of(user)`: `users.roles`, при пустом — `[users.role]`. Пользователь читается из базы на каждом запросе (`get_current_user` уже фильтрует `is_active`), поэтому смена ролей и выключение действуют сразу; выключенный → 401.
- Зависимости-метки: `require_admin`, `require_roles(*roles)` (достаточно любой роли), `require_staff`; алиасы `require_teacher` (admin+teacher) и `require_catalog_editor` (admin+methodist). Ученический сайт размечен `get_current_student`.
- Принадлежность — фильтром в SQL: `owned_student_ids(user)` (SELECT для `.in_()`), `owned_student_filter`, `student_or_404(db, user, id)`, `substitute_lessons(user)`, `effective_teacher_sql()` (= `coalesce(lessons.teacher_id, students.teacher_id)`), `active_teacher_or_400`.
- **Единая разметка**: у каждого маршрута в дереве зависимостей (роутер или маршрут) должна быть зависимость с атрибутом `__access_class__`; исключения — явный `PUBLIC_ROUTES`. `unmarked_routes(app)` + тест `test_every_route_has_an_access_class` (и тест, что «голый» маршрут ловится).
- Старые `require_role` / `teacher_or_admin` в `core/dependencies.py` теперь тоже понимают набор ролей (для admin ничего не меняется).

### 3. Классификация маршрутов — сделано
- Старые роутеры `students` (вкл. `/parents`), `lessons`, `manual_lessons`, `finance`, `events`, `recurring_events`, `notifications`, `upcoming`, `tasks` — `dependencies=[Depends(require_admin)]` на уровне роутера. Проверки внутри маршрутов не трогались.
- `/auth/me` — любой сотрудник (`require_staff`); `/auth/login|refresh|logout`, `/health`, `/student/auth/login` — публичные.
- `/lms`: чтение каталога (`GET /lms/courses`, `/lms/courses/{id}`, `/lms/lessons/{id}`) — admin/teacher/methodist; запись каталога (курсы, модули, уроки, материалы, задания, reorder) — admin/methodist; `GET /lms/students/{id}/enrollments`, `…/courses/{id}/progress` и заметки (`GET/POST /lms/students/{id}/notes`, `PATCH/DELETE /lms/notes/{id}`) — admin/teacher (teacher: свои ученики, иначе 404); любая запись по ученику (назначить/сменить/снять курс, статус, прогресс урока) — admin.
- Доступы учеников: `GET /students/{id}/access`, `GET …/access/password`, `POST …/reset-password`, `POST …/unlock` — admin/teacher (свои); создать, `PATCH`, `login-suggestion` — admin.

### 4. `/teacher/*` — сделано (только чтение, свои схемы в `schemas/staff.py`)
- `GET /teacher/me` — `id`, `full_name`, `roles`, `students_count` (активные свои).
- `GET /teacher/students?active_only=true` — `id`, `full_name`, `level`, `course`, `is_active`, `has_access`, `parents[]` (`full_name`, `phone`, `whatsapp_phone`).
- `GET /teacher/students/{id}` — то же + `schedule[]` (активные слоты: `day_of_week`, `start_time`, `duration_minutes`, `valid_from`, `valid_until`). Чужой → 404.
- `GET /teacher/lessons?from=&to=` — уроки, где `coalesce(lessons.teacher_id, students.teacher_id) = я`: `id`, `is_substitute`, `student_id`, `student_name`, `course`, `start_time`, `end_time`, `status`, `topic`, `lesson_type`. Для замены `student_id` и `topic` = `null` (только имя и курс). По умолчанию сегодня + 6 дней, максимум 62 дня.
- Денег, `notes`, `birth_date`, `teacher_notes`, личных событий, задач и уведомлений в схемах нет вообще.
- admin на `/teacher/*` видит всех учеников и все уроки (замен нет).

### 5. Сотрудники — сделано (`routers/staff.py`, только admin)
- `GET /staff` (пользователи с ролями admin/teacher/methodist, `students_count`), `POST /staff` (email, `full_name`, `phone`, `roles`, необязательный `password` ≥ 8; иначе генерируется 12 символов; показывается один раз, `Cache-Control: no-store`, в базе только bcrypt; email приводится к нижнему регистру, дубликат → 409), `PATCH /staff/{id}` (`full_name`, `phone`, `roles`, `is_active`), `POST /staff/{id}/reset-password` (пароль от админа или сгенерированный; удаляет refresh-токены пользователя).
- `users.role` синхронизируется с набором ролей (главная: admin > teacher > methodist), чтобы CRM, читающая `role` из `/auth/me`, работала как раньше.
- `PUT /students/{id}/teacher` и `PUT /lessons/{id}/teacher` (`teacher_id` или `null`): преподаватель должен быть активным и с ролью `teacher` (иначе 400). Замена — только для будущего урока со статусом `scheduled`/`rescheduled` (иначе 409): проведённый урок историю не меняет.
- **Правило увольнения**: выключение или снятие роли `teacher`, если за пользователем есть ученики (`students.teacher_id`, включая неактивных) или будущие замены, → `409 {"code": "has_students", "blockers": [{kind: student|lesson, id, name, start_time}]}`. Иначе `is_active=false` и **все refresh-токены удаляются**; access-токены перестают работать сразу (проверка `is_active` при каждом запросе). Последнего активного admin нельзя выключить или лишить роли admin (`409 last_admin`).
- Конфликтов путей нет: `PUT /students/{id}/teacher` и `PUT /lessons/{id}/teacher` не пересекаются с существующими методами на тех же путях.

### 6. Пароли учеников: просмотр и снятие блокировки — сделано
- `core/student_secrets.py`: Fernet (`cryptography`), ключ `STUDENT_PASSWORD_KEY`. Для dev ключ сгенерирован и записан в `itdeti/.env` (не коммитится); в `.env.example` — только имя.
- Создание и сброс пароля заполняют и `password_hash`, и `password_enc`.
- `GET /students/{id}/access/password` → `{student_id, login, password}`, `Cache-Control: no-store`. Аккаунт без `password_enc` (ночь 1) или зашифрованный другим ключом → `409 password_not_stored` «Пароль недоступен для просмотра, сбросьте». Без ключа → `503 password_view_disabled`, остальное работает (сброс показывает пароль один раз, `password_enc` пустой).
- `POST /students/{id}/access/unlock` — обнуляет `failed_attempts` и `locked_until`. Автоснятие через 15 минут не менялось.
- В лог пишется только факт: `user <id> viewed the password of student <id>` (логгер `itdeti.student_access`), без пароля — проверено тестом.

### 7. Фактический преподаватель проведённого урока — сделано
Урок становится «проведённым» в трёх местах; в каждое добавлена одна строка рядом со списанием, сама логика списания не тронута:
- `core/lesson_service.complete_overdue_lessons` (система по календарю): `if lesson.teacher_id is None: lesson.teacher_id = student.teacher_id`;
- `PATCH /lessons/{id}` со `status=completed` (админ): то же;
- `POST /manual-lessons` (урок создаётся сразу проведённым): `teacher_id=student.teacher_id`.
Тест `test_held_lesson_records_teacher_and_deduction_is_unchanged`: баланс уменьшается ровно на цену урока (как раньше), записывается постоянный преподаватель, у замены остаётся заместитель, последующая смена преподавателя ученика историю не меняет; в ответе admin поле `teacher_id` не появилось.

### 8. Тестовые данные и демонстрация — сделано
- `scripts/seed_dev.py` (идемпотентный, защита dev): преподаватели A и B, методист, «преподаватель + методист»; ученики: Алия и Тимур — у A, Егор и Дана — у B, Максим — без преподавателя; родители у Алии и Егора; расписание у двух учеников; одна будущая замена (урок Тимура ведёт B); пароли учеников хранятся и в зашифрованном виде. Пароли сотрудников (только dev) — в docstring скрипта.
- `scripts/demo_roles.py`: входит через настоящий `POST /auth/login` под каждой ролью и печатает таблицу (раздел 3).

### 9. Тесты — сделано, все проходят (21 новый + 57 старых)
`tests/test_access_model.py` (dev-база, свои данные с уникальным суффиксом, удаляются в конце):
- разметка: нет неразмеченных маршрутов; «голый» маршрут ловится; `roles_of` с откатом на `role`;
- **матрица**: каждый маршрут × {anonymous, student, teacher A, methodist, teacher+methodist, admin}; ожидание выводится из класса маршрута; разрешено → не 401/403, запрещено → 401/403/404 (около 800 запросов);
- изоляция A ↔ B по всем маршрутам с `student_id`, по `/lms/notes/{id}`, по спискам (`/teacher/*`), телефон родителя B и заметка про B нигде не встречаются;
- **никаких денег**: автоматический обход всех GET-маршрутов, доступных teacher (+ ответы POST заметки, сброса, разблокировки); рекурсивно нет ключей `balance`, `lesson_price`, `price`, `amount`, `payments`, `balance_deducted`, `current_month_due`, `monthly_debt`, `payments_this_month`, `remaining_lessons`, `income`, `expenses`, `total`; в `/teacher/*` ещё нет `notes`, `birth_date`, `teacher_notes` и их значений;
- финансы, уведомления, задачи, события, расписание, `/staff`, `/students` → 403 для teacher, methodist, teacher+methodist;
- методист: каталог читает и пишет (создать/изменить/удалить курс), учеников не видит; teacher+methodist: обе роли, но не больше;
- замена: заместитель видит урок (имя и курс, без `student_id`/`topic`), карточка ученика → 404; постоянный преподаватель видит ученика;
- назначение преподавателя и замены (400 для не-преподавателя, 409 для прошедшего урока, 403 для teacher);
- увольнение: 409 с перечнем (ученик + замена), после переназначения — 200; старый access-токен → 401, refresh → 401, refresh-токенов 0, вход → 401; смена ролей действует на уже выданный токен; последний admin (через подмену счётчика) → 409;
- пароли: teacher видит пароль своего ученика, чужого → 404, методист → 403; сброс меняет и хеш, и `password_enc`; аккаунт ночи 1 → 409 `password_not_stored`; разблокировка; без ключа → 503; в логе нет пароля.

**Изменённый старый тест (намеренное изменение поведения):** `tests/test_student_site.py::test_admin_creates_resets_and_disables_access` — `GET /students/{чужой}/access` для преподавателя теперь **404** вместо 403 (ТЗ: преподаватель видит доступ своих учеников, чужое → 404). Остальные 56 старых тестов не менялись.

**Как прогонялось и что пошло не так.** Тесты медленные: каждый запрос идёт в dev-базу в Сеуле (1–2 с). Первый полный прогон `test_access_model.py` упёрся в мой лимит 25 минут: 7 тестов прошли, процесс был убит, и очистка не успела. Остатки (5 учеников, 7 пользователей, 1 курс, 4 урока с суффиксом того прогона) я удалил отдельным скриптом по шаблону имён. После этого матрица ускорена: вместо случайного UUID в путь подставляется `not-a-uuid`, и разрешённая роль получает 422 сразу после проверки доступа, без запуска обработчика. Итоговые прогоны: 20 тестов без матрицы — 12 мин, все прошли; матрица — 22 мин, прошла; 57 старых — 3,5 мин, все прошли. Каждый из этих прогонов удалил свои данные за собой.


## 3. Как запустить и проверить
```bash
cd itdeti
# .env уже есть (dev); для просмотра паролей нужен STUDENT_PASSWORD_KEY (в dev .env записан)
.venv/Scripts/python scripts/apply_migration.py 012 013     # уже применены к dev; повтор безопасен
.venv/Scripts/python scripts/seed_dev.py
.venv/Scripts/python scripts/demo_roles.py                  # таблица «что видит каждая роль», ~2–3 мин
.venv/Scripts/python -m pytest -q -p no:cacheprovider       # все тесты, ~40 мин на dev-базе в Сеуле (матрица маршрутов одна ~22 мин; `-k "not route_matrix"` — ~12 мин)
.venv/Scripts/python scripts/routes_inventory.py            # таблица маршрутов и классов
.venv/Scripts/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
Рекомендую `PYTHONDONTWRITEBYTECODE=1` (в репозитории закоммичены `__pycache__`, см. раздел 7).

Вывод `demo_roles.py` на dev:
```
DISABLE_SCHEDULER=true: background jobs are not started
(catalog course used: Python (dev seed))
                  | /students (CRM) | /finance/summary | /tasks/projects | /staff | /teacher/students | /teacher/lessons | Алия (A) card | Егор (B) card | Алия password | LMS catalog read | LMS course create
------------------+-----------------+------------------+-----------------+--------+-------------------+------------------+---------------+---------------+---------------+------------------+------------------
admin             | 200 ✓           | 200 ✓            | 200 ✓           | 200 ✓  | 9 students        | 4 (0 subst.)     | 200 ✓         | 200 ✓         | 200 ✓         | 200 ✓            | allowed          
teacher A         | 403 ✗           | 403 ✗            | 403 ✗           | 403 ✗  | 2 students        | 2 (0 subst.)     | 200 ✓         | 404 ✗         | 200 ✓         | 200 ✓            | 403 ✗            
teacher B         | 403 ✗           | 403 ✗            | 403 ✗           | 403 ✗  | 2 students        | 2 (1 subst.)     | 404 ✗         | 200 ✓         | 404 ✗         | 200 ✓            | 403 ✗            
methodist         | 403 ✗           | 403 ✗            | 403 ✗           | 403 ✗  | 403 ✗             | 403 ✗            | 403 ✗         | 403 ✗         | 403 ✗         | 200 ✓            | allowed          
teacher+methodist | 403 ✗           | 403 ✗            | 403 ✗           | 403 ✗  | 0 students        | 0 (0 subst.)     | 404 ✗         | 404 ✗         | 404 ✗         | 200 ✓            | allowed          

Expected: only admin gets 200 on /students, /finance, /tasks, /staff; teachers see only own students (A: Алия + Тимур, B: Егор + Дана), a foreign card is 404; teacher B sees Тимур's lesson as a substitution; the methodist reads the catalog but no students; teacher+methodist has no students of his own.
```

Ручная проверка в `/docs`: войти `POST /auth/login` под `teacher.a.dev@example.com` (пароль в `scripts/seed_dev.py`), «Authorize», затем `GET /teacher/students`, `GET /students/{id Егора}/access` (404), `GET /finance/summary` (403).

## 4. Все маршруты и класс доступа
Полная таблица (то же, что `itdeti/docs/ROUTES_ACCESS.md`; «Before night 2» — защита до этой ночи, «Access» — сейчас). Классы — в разделе 2.2.

Всего: 138 строк (метод + путь), из них новых: 12.

| Router | Method | Path | Before night 2 | Access |
|---|---|---|---|---|
| auth | POST | `/auth/login` | NONE | public |
| auth | POST | `/auth/logout` | NONE | public |
| auth | GET | `/auth/me` | any CRM user | roles: admin, teacher, methodist |
| auth | POST | `/auth/refresh` | NONE | public |
| events | GET | `/events` | any CRM user | admin |
| events | POST | `/events` | role in admin/teacher | admin |
| events | DELETE | `/events/{event_id}` | role in admin/teacher | admin |
| events | GET | `/events/{event_id}` | any CRM user | admin |
| events | PATCH | `/events/{event_id}` | role in admin/teacher | admin |
| fastapi (docs) | GET | `/docs` | public (docs) | public |
| fastapi (docs) | GET | `/docs/oauth2-redirect` | public (docs) | public |
| fastapi (docs) | GET | `/openapi.json` | public (docs) | public |
| fastapi (docs) | GET | `/redoc` | public (docs) | public |
| finance | GET | `/finance/expenses` | any CRM user | admin |
| finance | POST | `/finance/expenses` | role in admin/teacher | admin |
| finance | DELETE | `/finance/expenses/{expense_id}` | role in admin/teacher | admin |
| finance | PATCH | `/finance/expenses/{expense_id}` | role in admin/teacher | admin |
| finance | GET | `/finance/payments` | any CRM user | admin |
| finance | POST | `/finance/payments` | role in admin/teacher | admin |
| finance | DELETE | `/finance/payments/{payment_id}` | role in admin/teacher | admin |
| finance | PATCH | `/finance/payments/{payment_id}` | role in admin/teacher | admin |
| finance | GET | `/finance/recurring-expenses` | any CRM user | admin |
| finance | POST | `/finance/recurring-expenses` | role in admin/teacher | admin |
| finance | POST | `/finance/recurring-expenses/sync` | role in admin/teacher | admin |
| finance | DELETE | `/finance/recurring-expenses/{recurring_id}` | role in admin/teacher | admin |
| finance | PATCH | `/finance/recurring-expenses/{recurring_id}` | role in admin/teacher | admin |
| finance | POST | `/finance/recurring-expenses/{recurring_id}/record` | role in admin/teacher | admin |
| finance | GET | `/finance/recurring-income-records` | any CRM user | admin |
| finance | DELETE | `/finance/recurring-income-records/{record_id}` | role in admin/teacher | admin |
| finance | PATCH | `/finance/recurring-income-records/{record_id}` | role in admin/teacher | admin |
| finance | GET | `/finance/recurring-incomes` | any CRM user | admin |
| finance | POST | `/finance/recurring-incomes` | role in admin/teacher | admin |
| finance | DELETE | `/finance/recurring-incomes/{recurring_id}` | role in admin/teacher | admin |
| finance | PATCH | `/finance/recurring-incomes/{recurring_id}` | role in admin/teacher | admin |
| finance | POST | `/finance/recurring-incomes/{recurring_id}/record` | role in admin/teacher | admin |
| finance | GET | `/finance/summary` | any CRM user | admin |
| finance | GET | `/finance/transactions` | any CRM user | admin |
| lessons | GET | `/lessons` | any CRM user | admin |
| lessons | POST | `/lessons` | role in admin/teacher | admin |
| lessons | DELETE | `/lessons/{lesson_id}` | role in admin/teacher | admin |
| lessons | PATCH | `/lessons/{lesson_id}` | role in admin/teacher | admin |
| lessons | POST | `/lessons/{lesson_id}/cancel` | role in admin/teacher | admin |
| lessons | GET | `/schedule/month` | any CRM user | admin |
| lessons | GET | `/schedule/today` | any CRM user | admin |
| lessons | GET | `/schedule/week` | any CRM user | admin |
| lms | GET | `/lms/courses` | role in admin/teacher | roles: admin, teacher, methodist |
| lms | POST | `/lms/courses` | role in admin/teacher | roles: admin, methodist |
| lms | DELETE | `/lms/courses/{course_id}` | role in admin/teacher | roles: admin, methodist |
| lms | GET | `/lms/courses/{course_id}` | role in admin/teacher | roles: admin, teacher, methodist |
| lms | PATCH | `/lms/courses/{course_id}` | role in admin/teacher | roles: admin, methodist |
| lms | POST | `/lms/courses/{course_id}/modules` | role in admin/teacher | roles: admin, methodist |
| lms | POST | `/lms/courses/{course_id}/modules/reorder` | role in admin/teacher | roles: admin, methodist |
| lms | DELETE | `/lms/enrollments/{enrollment_id}` | role in admin/teacher | admin |
| lms | PATCH | `/lms/enrollments/{enrollment_id}` | role in admin/teacher | admin |
| lms | DELETE | `/lms/lessons/{lesson_id}` | role in admin/teacher | roles: admin, methodist |
| lms | GET | `/lms/lessons/{lesson_id}` | role in admin/teacher | roles: admin, teacher, methodist |
| lms | PATCH | `/lms/lessons/{lesson_id}` | role in admin/teacher | roles: admin, methodist |
| lms | POST | `/lms/lessons/{lesson_id}/materials` | role in admin/teacher | roles: admin, methodist |
| lms | POST | `/lms/lessons/{lesson_id}/tasks` | role in admin/teacher | roles: admin, methodist |
| lms | DELETE | `/lms/materials/{material_id}` | role in admin/teacher | roles: admin, methodist |
| lms | PATCH | `/lms/materials/{material_id}` | role in admin/teacher | roles: admin, methodist |
| lms | DELETE | `/lms/modules/{module_id}` | role in admin/teacher | roles: admin, methodist |
| lms | PATCH | `/lms/modules/{module_id}` | role in admin/teacher | roles: admin, methodist |
| lms | POST | `/lms/modules/{module_id}/lessons` | role in admin/teacher | roles: admin, methodist |
| lms | POST | `/lms/modules/{module_id}/lessons/reorder` | role in admin/teacher | roles: admin, methodist |
| lms | DELETE | `/lms/notes/{note_id}` | role in admin/teacher | roles: admin, teacher |
| lms | PATCH | `/lms/notes/{note_id}` | role in admin/teacher | roles: admin, teacher |
| lms | PUT | `/lms/students/{student_id}/course` | role in admin/teacher | admin |
| lms | GET | `/lms/students/{student_id}/courses/{course_id}/progress` | role in admin/teacher | roles: admin, teacher |
| lms | GET | `/lms/students/{student_id}/enrollments` | role in admin/teacher | roles: admin, teacher |
| lms | POST | `/lms/students/{student_id}/enrollments` | role in admin/teacher | admin |
| lms | PUT | `/lms/students/{student_id}/lessons/{lesson_id}/progress` | role in admin/teacher | admin |
| lms | GET | `/lms/students/{student_id}/notes` | role in admin/teacher | roles: admin, teacher |
| lms | POST | `/lms/students/{student_id}/notes` | role in admin/teacher | roles: admin, teacher |
| lms | DELETE | `/lms/tasks/{task_id}` | role in admin/teacher | roles: admin, methodist |
| lms | PATCH | `/lms/tasks/{task_id}` | role in admin/teacher | roles: admin, methodist |
| main | GET | `/health` | NONE | public |
| manual_lessons | POST | `/manual-lessons` | role in admin/teacher | admin |
| notifications | GET | `/notifications` | any CRM user | admin |
| notifications | POST | `/notifications` | role in admin/teacher | admin |
| notifications | POST | `/notifications/{notification_id}/confirm` | role in admin/teacher | admin |
| recurring_events | GET | `/recurring-events` | any CRM user | admin |
| recurring_events | POST | `/recurring-events` | role in admin/teacher | admin |
| recurring_events | DELETE | `/recurring-events/{recurring_id}` | role in admin/teacher | admin |
| recurring_events | DELETE | `/recurring-events/{recurring_id}/all` | role in admin/teacher | admin |
| recurring_events | GET | `/recurring-events/{recurring_id}/events` | any CRM user | admin |
| staff | PUT | `/lessons/{lesson_id}/teacher` | — (new) | admin |
| staff | GET | `/staff` | — (new) | admin |
| staff | POST | `/staff` | — (new) | admin |
| staff | PATCH | `/staff/{user_id}` | — (new) | admin |
| staff | POST | `/staff/{user_id}/reset-password` | — (new) | admin |
| staff | PUT | `/students/{student_id}/teacher` | — (new) | admin |
| student_access | GET | `/students/{student_id}/access` | role in admin | roles: admin, teacher |
| student_access | PATCH | `/students/{student_id}/access` | role in admin | admin |
| student_access | POST | `/students/{student_id}/access` | role in admin | admin |
| student_access | GET | `/students/{student_id}/access/login-suggestion` | role in admin | admin |
| student_access | GET | `/students/{student_id}/access/password` | — (new) | roles: admin, teacher |
| student_access | POST | `/students/{student_id}/access/reset-password` | role in admin | roles: admin, teacher |
| student_access | POST | `/students/{student_id}/access/unlock` | — (new) | roles: admin, teacher |
| student_site | POST | `/student/auth/login` | NONE | public |
| student_site | GET | `/student/courses` | student token | student |
| student_site | GET | `/student/courses/{course_id}` | student token | student |
| student_site | GET | `/student/lessons/{lesson_id}` | student token | student |
| student_site | POST | `/student/lessons/{lesson_id}/progress` | student token | student |
| student_site | GET | `/student/me` | student token | student |
| students | GET | `/parents` | any CRM user | admin |
| students | POST | `/parents` | role in admin/teacher | admin |
| students | GET | `/parents/{parent_id}` | any CRM user | admin |
| students | PATCH | `/parents/{parent_id}` | role in admin/teacher | admin |
| students | GET | `/students` | any CRM user | admin |
| students | POST | `/students` | role in admin/teacher | admin |
| students | DELETE | `/students/{student_id}` | role in admin/teacher | admin |
| students | GET | `/students/{student_id}` | any CRM user | admin |
| students | PATCH | `/students/{student_id}` | role in admin/teacher | admin |
| students | GET | `/students/{student_id}/balance` | any CRM user | admin |
| students | DELETE | `/students/{student_id}/parents/{parent_id}` | role in admin/teacher | admin |
| students | POST | `/students/{student_id}/parents/{parent_id}` | role in admin/teacher | admin |
| students | GET | `/students/{student_id}/schedule` | any CRM user | admin |
| students | POST | `/students/{student_id}/schedule` | role in admin/teacher | admin |
| students | DELETE | `/students/{student_id}/schedule/{slot_id}` | role in admin/teacher | admin |
| students | PATCH | `/students/{student_id}/schedule/{slot_id}` | role in admin/teacher | admin |
| tasks | POST | `/tasks` | role in admin/teacher | admin |
| tasks | GET | `/tasks/feed` | role in admin/teacher | admin |
| tasks | GET | `/tasks/list` | role in admin/teacher | admin |
| tasks | GET | `/tasks/projects` | role in admin/teacher | admin |
| tasks | POST | `/tasks/projects` | role in admin/teacher | admin |
| tasks | DELETE | `/tasks/projects/{project_id}` | role in admin/teacher | admin |
| tasks | PATCH | `/tasks/projects/{project_id}` | role in admin/teacher | admin |
| tasks | DELETE | `/tasks/{task_id}` | role in admin/teacher | admin |
| tasks | PATCH | `/tasks/{task_id}` | role in admin/teacher | admin |
| tasks | POST | `/tasks/{task_id}/done` | role in admin/teacher | admin |
| tasks | POST | `/tasks/{task_id}/move` | role in admin/teacher | admin |
| tasks | POST | `/tasks/{task_id}/skip` | role in admin/teacher | admin |
| teacher | GET | `/teacher/lessons` | — (new) | roles: admin, teacher |
| teacher | GET | `/teacher/me` | — (new) | roles: admin, teacher |
| teacher | GET | `/teacher/students` | — (new) | roles: admin, teacher |
| teacher | GET | `/teacher/students/{student_id}` | — (new) | roles: admin, teacher |
| upcoming | GET | `/schedule/upcoming` | any CRM user | admin |

## 5. Порядок выкладки на прод
1. Бэкап прод-базы.
2. Миграции по порядку: `012_student_accounts.sql`, затем `013_access_model.sql` (SQL-редактор Supabase прод-проекта; `apply_migration.py` к проду не подключится намеренно). Обе идемпотентны и только добавляют.
3. Переменная `STUDENT_PASSWORD_KEY` на Render (новый ключ, не dev; хранить как секрет; потеря ключа = пароли нельзя посмотреть, только сбросить).
4. Код (ветка `lms-mvp`). **Не раньше миграций:** модели теперь читают `users.roles`, `lessons.teacher_id`, `students.teacher_id` — без миграций упадут вход в CRM и почти все маршруты.
5. Проверка после выкладки: вход admin в CRM, `/auth/me`, календарь, финансы, Android-приложение (работает под admin, маршруты прежние).
Также до деплоя остаётся из ночи 1: `--proxy-headers` для ограничителя частоты на Render.

## 6. Решения, принятые мной, и вопросы владельцу
Решения (при сомнении — строже):
1. **Поля для преподавателя**: см. раздел 2.4. Не отдаются: баланс, цена, оплаты, суммы, `students.notes` (заметки админа в карточке), `birth_date`, `teacher_notes` урока, email родителя. Родитель: только имя, телефон, WhatsApp.
2. **`level`**: ТЗ просит поле `level`; в модели его не было, но колонка `students.level` есть в базе (миграция 001). Добавил в модель `Student` (без изменения ответов admin) и отдаю в `/teacher/students`.
3. **Замена**: заместитель получает только `id` урока, время, статус, тип, имя ученика и курс; `student_id` и `topic` — `null`. Постоянный преподаватель видит своего ученика, но урок, отданный на замену, в его `/teacher/lessons` не попадает (урок «принадлежит» заместителю: `coalesce(lessons.teacher_id, students.teacher_id)`). Проведённые уроки после смены преподавателя остаются у фактического преподавателя (новый преподаватель их не видит в `/teacher/lessons`, прежний видит как «замену» — только имя и курс).
4. **`/teacher/lessons` делает то же обслуживание календаря, что календарь admin** (`complete_overdue_lessons` + генерация уроков из расписания), иначе преподаватель видел бы неполный и устаревший календарь. Сам преподаватель ничего не меняет; это то же «система отмечает по календарю». Диапазон ограничен 62 днями (защита от генерации уроков на годы вперёд). **Вопрос:** устраивает ли, или преподавателю показывать только уже созданные уроки без побочных действий?
5. **Увольнение блокируют и неактивные ученики** с `teacher_id` этого преподавателя (строже); переназначить можно `PUT /students/{id}/teacher` с `null`.
6. **Сброс пароля сотрудника** удаляет его refresh-токены (выход со всех устройств после смены пароля). Смена ролей токены не удаляет — права пересчитываются на каждом запросе.
7. **Email сотрудника** приводится к нижнему регистру при создании; `/auth/login` сравнивает email точно (не менял — это существующий вход), значит сотрудник вводит email в нижнем регистре.
8. **`/auth/me`** — класс «любой сотрудник» (admin/teacher/methodist). Пользователи с ролью `parent`/`student` в `users` теперь получают 403 почти везде (раньше GET-маршруты пускали любого).
9. **Методист** в CRM ничего, кроме каталога LMS и `/auth/me`, не получает (по «Модели доступа»: «методист без роли преподавателя в CRM не входит»). Вход (`/auth/login`) у него работает — сервер сам ограничивает.
10. Отказ по роли — **403**, чужой объект при разрешённой роли — **404**. Для admin несуществующий объект — как раньше.
11. Просмотр пароля без ключа — **503** `password_view_disabled` (ТЗ: «понятная ошибка»); зашифрованный другим ключом — как «не сохранён» (409).
12. `GET /staff` показывает пользователей с ролями admin/teacher/methodist (родители из `users` не попадают).

Вопросы:
1. Пункт 4 выше (побочные действия `/teacher/lessons`).
2. Должен ли новый постоянный преподаватель видеть историю проведённых уроков ученика (сейчас — нет, уроки привязаны к фактическому преподавателю)?
3. Нужен ли преподавателю email родителя (сейчас не отдаётся)?
4. Нужно ли запретить admin выключать самого себя, если он не последний admin (сейчас можно)?
5. Пароль сотрудника при создании задаётся админом или генерируется — нужно ли требование сложности (сейчас ≥ 8 символов)?

## 7. Противоречия ТЗ и заметок (сделано по ТЗ)
1. «Модель доступа»: «Доступ ученика: создать, **выключить** — только admin»; ТЗ уточняет: `PATCH` (логин, `is_active`) — admin. Совпадает; «Снять блокировку» и просмотр пароля — admin+teacher, как в обоих.
2. «Модель доступа»: «Пользователям с ролью teacher или methodist до ночи 2 в базе не создавать» — в dev-базе они созданы в ночь 2 (seed и тесты), это и есть ночь 2.
3. «Решения»/«Роли и вход учеников»: «вход один, роль определяет, куда попасть» — по ТЗ ночи 2 вход сотрудников на платформу вне рамок, ученический вход остаётся отдельным.
4. «Модель данных» говорит о каскадных удалениях `lms_*`; в dev-базе их нет (см. ночь 1), тесты удаляют свои данные явно.
5. ТЗ требует `level` в `/teacher/students`; в заметках поле не упоминается, в модели его не было — см. решение 2.
6. Миграция 012: в её комментарии остался старый путь `migrations/001_student_accounts.sql` — ТЗ запрещает менять содержимое при переносе; актуальная команда: `apply_migration.py 012`.

## 8. Найденные проблемы в существующем коде (только описаны)
1. **Файлы миграций 008 и 011 не включают RLS** для своих таблиц (`lms_*`, `task_projects`, `tasks`, `task_occurrences`). В dev RLS на всех таблицах включён (проверено по `pg_class.relrowsecurity`), на проде — **проверить**. Новый `apply_migration.py` такие файлы отклоняет.
2. `/auth/refresh` не проверяет `users.is_active` (выдаёт новый access-токен выключенному пользователю; тот, правда, сразу получает 401 в `get_current_user`). При увольнении через `/staff` refresh-токены удаляются, но при выключении другим путём (SQL) — нет.
3. `/auth/login` сравнивает email с учётом регистра.
4. GET-маршруты календаря admin (`/lessons`, `/schedule/*`) меняют данные: отмечают проведённые уроки и списывают баланс, создают уроки из расписания. Это и есть «система», но побочные действия в GET неожиданны (повторы, параллельные запросы — защищены `FOR UPDATE`).
5. JWT CRM несёт `role` из `users.role`; для прав он больше не используется (роли читаются из базы), но остаётся в токене.
6. Все пункты раздела 4 отчёта ночи 1 остаются в силе, кроме п. 1 (права на чтение — закрыто этой ночью) и частично п. 8 (блокировка входа в CRM — по ТЗ отложена). В частности: CORS `*` с `credentials`, `create_all` при `APP_ENV=development`, `requirements.txt` не ставится на Python 3.13, закоммиченные `__pycache__`, расхождения dev-схемы с миграцией 008, refresh-токены открытым текстом.
7. Вход в CRM без ограничения частоты: теперь за паролем преподавателя — контакты родителей; по решению владельца блокировку делаем до найма первого преподавателя.

## 9. Что не сделано / вне рамок
- Интерфейс (CRM и платформа), блокировка входа в CRM и журнал действий, вход сотрудников на ученический сайт, ДЗ, правка ✎, зарплата, CORS, деплой — по ТЗ вне рамок.
- Миграции 012 и 013 к проду не применялись; ветка не отправлена.
- `itdetiWeb`: блок доступа ученика (`student-access.js`) по-прежнему показывает пароль один раз и не знает о просмотре/разблокировке — это интерфейс ночи 3. Под admin он работает как раньше.
