# Маршруты API и классы доступа

Состояние после ночи 4 (ветка `lms-mvp`). Ночь 4: добавлен `GET /audit` (журнал действий, только admin); `POST /auth/login` остаётся `public`, но ограничен по частоте и блокируется после неверных паролей (`core/login_guard.py`: 429 с `Retry-After`). В ночь 3 маршруты не добавлялись, изменилась только документация FastAPI: `/docs`, `/redoc`, `/openapi.json` есть только при `ENABLE_DOCS=true`, иначе 404. Таблица сгенерирована: `python scripts/routes_inventory.py --before <снимок до ночи 2>`; колонка «Before night 2» — защита на начало ночи 2, «Access» — класс доступа сейчас.

## Классы доступа (`core/access.py`)

| Класс | Кто проходит | Как задан |
|---|---|---|
| `public` | все, без входа | явный список `PUBLIC_ROUTES` (`/health`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/student/auth/login`, документация FastAPI — только при `ENABLE_DOCS=true`) |
| `student` | только токен ученического сайта | зависимость `get_current_student` |
| `admin` | только роль `admin` | `require_admin` (у старых роутеров — на уровне роутера) |
| `roles: admin, teacher, methodist` | любой сотрудник | `require_staff` |
| `roles: admin, methodist` | правка каталога LMS | `require_catalog_editor` |
| `roles: admin, teacher` | admin — всё; teacher — **только свои ученики** (чужое → 404) | `require_teacher` + `student_or_404` / `owned_student_ids` в SQL |

Правила:
- Роли читаются из базы при каждом запросе (`users.roles`, при пустом — `[users.role]`); выключенный пользователь → 401.
- У нескольких ролей права складываются: класс `roles: …` пропускает, если есть **любая** из ролей.
- Маршрут без метки и не из `PUBLIC_ROUTES` — ошибка: `tests/test_access_model.py::test_every_route_has_an_access_class`.
- Отказ по роли → 403; чужой ученик, урок, заметка, доступ → 404.
- Для admin ответы и статусы прежние (старые проверки `get_current_user` / `teacher_or_admin` внутри маршрутов остались, сверху добавлен класс).

## Все маршруты

Всего: 139 строк (метод + путь), из них новых: 13 (в ночь 4 добавлен один маршрут: `GET /audit`).

| Router | Method | Path | Before night 2 | Access |
|---|---|---|---|---|
| audit | GET | `/audit` | — (new) | admin |
| auth | POST | `/auth/login` | NONE | public |
| auth | POST | `/auth/logout` | NONE | public |
| auth | GET | `/auth/me` | any CRM user | roles: admin, teacher, methodist |
| auth | POST | `/auth/refresh` | NONE | public |
| events | GET | `/events` | any CRM user | admin |
| events | POST | `/events` | role in admin/teacher | admin |
| events | DELETE | `/events/{event_id}` | role in admin/teacher | admin |
| events | GET | `/events/{event_id}` | any CRM user | admin |
| events | PATCH | `/events/{event_id}` | role in admin/teacher | admin |
| fastapi (docs) | GET | `/docs` | public (docs) | public, only with ENABLE_DOCS=true (404 otherwise) |
| fastapi (docs) | GET | `/docs/oauth2-redirect` | public (docs) | public, only with ENABLE_DOCS=true (404 otherwise) |
| fastapi (docs) | GET | `/openapi.json` | public (docs) | public, only with ENABLE_DOCS=true (404 otherwise) |
| fastapi (docs) | GET | `/redoc` | public (docs) | public, only with ENABLE_DOCS=true (404 otherwise) |
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
