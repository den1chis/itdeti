# Маршруты API и защита доступа

Инвентаризация на начало ночи 2 (ветка `lms-mvp`, до изменений модели доступа). Сгенерировано `python scripts/routes_inventory.py`.

Обозначения: `any CRM user` — только `get_current_user` (любой активный пользователь из `users`, без проверки роли); `role in …` — `require_role(...)`; `student token` — токен ученического сайта; `NONE` — без авторизации.

Всего строк (метод + путь): 126.

| Router | Method | Path | Access |
|---|---|---|---|
| auth | POST | `/auth/login` | NONE |
| auth | POST | `/auth/logout` | NONE |
| auth | GET | `/auth/me` | any CRM user |
| auth | POST | `/auth/refresh` | NONE |
| events | GET | `/events` | any CRM user |
| events | POST | `/events` | role in admin/teacher |
| events | DELETE | `/events/{event_id}` | role in admin/teacher |
| events | GET | `/events/{event_id}` | any CRM user |
| events | PATCH | `/events/{event_id}` | role in admin/teacher |
| fastapi (docs) | GET | `/docs` | public (docs) |
| fastapi (docs) | GET | `/docs/oauth2-redirect` | public (docs) |
| fastapi (docs) | GET | `/openapi.json` | public (docs) |
| fastapi (docs) | GET | `/redoc` | public (docs) |
| finance | GET | `/finance/expenses` | any CRM user |
| finance | POST | `/finance/expenses` | role in admin/teacher |
| finance | DELETE | `/finance/expenses/{expense_id}` | role in admin/teacher |
| finance | PATCH | `/finance/expenses/{expense_id}` | role in admin/teacher |
| finance | GET | `/finance/payments` | any CRM user |
| finance | POST | `/finance/payments` | role in admin/teacher |
| finance | DELETE | `/finance/payments/{payment_id}` | role in admin/teacher |
| finance | PATCH | `/finance/payments/{payment_id}` | role in admin/teacher |
| finance | GET | `/finance/recurring-expenses` | any CRM user |
| finance | POST | `/finance/recurring-expenses` | role in admin/teacher |
| finance | POST | `/finance/recurring-expenses/sync` | role in admin/teacher |
| finance | DELETE | `/finance/recurring-expenses/{recurring_id}` | role in admin/teacher |
| finance | PATCH | `/finance/recurring-expenses/{recurring_id}` | role in admin/teacher |
| finance | POST | `/finance/recurring-expenses/{recurring_id}/record` | role in admin/teacher |
| finance | GET | `/finance/recurring-income-records` | any CRM user |
| finance | DELETE | `/finance/recurring-income-records/{record_id}` | role in admin/teacher |
| finance | PATCH | `/finance/recurring-income-records/{record_id}` | role in admin/teacher |
| finance | GET | `/finance/recurring-incomes` | any CRM user |
| finance | POST | `/finance/recurring-incomes` | role in admin/teacher |
| finance | DELETE | `/finance/recurring-incomes/{recurring_id}` | role in admin/teacher |
| finance | PATCH | `/finance/recurring-incomes/{recurring_id}` | role in admin/teacher |
| finance | POST | `/finance/recurring-incomes/{recurring_id}/record` | role in admin/teacher |
| finance | GET | `/finance/summary` | any CRM user |
| finance | GET | `/finance/transactions` | any CRM user |
| lessons | GET | `/lessons` | any CRM user |
| lessons | POST | `/lessons` | role in admin/teacher |
| lessons | DELETE | `/lessons/{lesson_id}` | role in admin/teacher |
| lessons | PATCH | `/lessons/{lesson_id}` | role in admin/teacher |
| lessons | POST | `/lessons/{lesson_id}/cancel` | role in admin/teacher |
| lessons | GET | `/schedule/month` | any CRM user |
| lessons | GET | `/schedule/today` | any CRM user |
| lessons | GET | `/schedule/week` | any CRM user |
| lms | GET | `/lms/courses` | role in admin/teacher |
| lms | POST | `/lms/courses` | role in admin/teacher |
| lms | DELETE | `/lms/courses/{course_id}` | role in admin/teacher |
| lms | GET | `/lms/courses/{course_id}` | role in admin/teacher |
| lms | PATCH | `/lms/courses/{course_id}` | role in admin/teacher |
| lms | POST | `/lms/courses/{course_id}/modules` | role in admin/teacher |
| lms | POST | `/lms/courses/{course_id}/modules/reorder` | role in admin/teacher |
| lms | DELETE | `/lms/enrollments/{enrollment_id}` | role in admin/teacher |
| lms | PATCH | `/lms/enrollments/{enrollment_id}` | role in admin/teacher |
| lms | DELETE | `/lms/lessons/{lesson_id}` | role in admin/teacher |
| lms | GET | `/lms/lessons/{lesson_id}` | role in admin/teacher |
| lms | PATCH | `/lms/lessons/{lesson_id}` | role in admin/teacher |
| lms | POST | `/lms/lessons/{lesson_id}/materials` | role in admin/teacher |
| lms | POST | `/lms/lessons/{lesson_id}/tasks` | role in admin/teacher |
| lms | DELETE | `/lms/materials/{material_id}` | role in admin/teacher |
| lms | PATCH | `/lms/materials/{material_id}` | role in admin/teacher |
| lms | DELETE | `/lms/modules/{module_id}` | role in admin/teacher |
| lms | PATCH | `/lms/modules/{module_id}` | role in admin/teacher |
| lms | POST | `/lms/modules/{module_id}/lessons` | role in admin/teacher |
| lms | POST | `/lms/modules/{module_id}/lessons/reorder` | role in admin/teacher |
| lms | DELETE | `/lms/notes/{note_id}` | role in admin/teacher |
| lms | PATCH | `/lms/notes/{note_id}` | role in admin/teacher |
| lms | PUT | `/lms/students/{student_id}/course` | role in admin/teacher |
| lms | GET | `/lms/students/{student_id}/courses/{course_id}/progress` | role in admin/teacher |
| lms | GET | `/lms/students/{student_id}/enrollments` | role in admin/teacher |
| lms | POST | `/lms/students/{student_id}/enrollments` | role in admin/teacher |
| lms | PUT | `/lms/students/{student_id}/lessons/{lesson_id}/progress` | role in admin/teacher |
| lms | GET | `/lms/students/{student_id}/notes` | role in admin/teacher |
| lms | POST | `/lms/students/{student_id}/notes` | role in admin/teacher |
| lms | DELETE | `/lms/tasks/{task_id}` | role in admin/teacher |
| lms | PATCH | `/lms/tasks/{task_id}` | role in admin/teacher |
| main | GET | `/health` | NONE |
| manual_lessons | POST | `/manual-lessons` | role in admin/teacher |
| notifications | GET | `/notifications` | any CRM user |
| notifications | POST | `/notifications` | role in admin/teacher |
| notifications | POST | `/notifications/{notification_id}/confirm` | role in admin/teacher |
| recurring_events | GET | `/recurring-events` | any CRM user |
| recurring_events | POST | `/recurring-events` | role in admin/teacher |
| recurring_events | DELETE | `/recurring-events/{recurring_id}` | role in admin/teacher |
| recurring_events | DELETE | `/recurring-events/{recurring_id}/all` | role in admin/teacher |
| recurring_events | GET | `/recurring-events/{recurring_id}/events` | any CRM user |
| student_access | GET | `/students/{student_id}/access` | role in admin |
| student_access | PATCH | `/students/{student_id}/access` | role in admin |
| student_access | POST | `/students/{student_id}/access` | role in admin |
| student_access | GET | `/students/{student_id}/access/login-suggestion` | role in admin |
| student_access | POST | `/students/{student_id}/access/reset-password` | role in admin |
| student_site | POST | `/student/auth/login` | NONE |
| student_site | GET | `/student/courses` | student token |
| student_site | GET | `/student/courses/{course_id}` | student token |
| student_site | GET | `/student/lessons/{lesson_id}` | student token |
| student_site | POST | `/student/lessons/{lesson_id}/progress` | student token |
| student_site | GET | `/student/me` | student token |
| students | GET | `/parents` | any CRM user |
| students | POST | `/parents` | role in admin/teacher |
| students | GET | `/parents/{parent_id}` | any CRM user |
| students | PATCH | `/parents/{parent_id}` | role in admin/teacher |
| students | GET | `/students` | any CRM user |
| students | POST | `/students` | role in admin/teacher |
| students | DELETE | `/students/{student_id}` | role in admin/teacher |
| students | GET | `/students/{student_id}` | any CRM user |
| students | PATCH | `/students/{student_id}` | role in admin/teacher |
| students | GET | `/students/{student_id}/balance` | any CRM user |
| students | DELETE | `/students/{student_id}/parents/{parent_id}` | role in admin/teacher |
| students | POST | `/students/{student_id}/parents/{parent_id}` | role in admin/teacher |
| students | GET | `/students/{student_id}/schedule` | any CRM user |
| students | POST | `/students/{student_id}/schedule` | role in admin/teacher |
| students | DELETE | `/students/{student_id}/schedule/{slot_id}` | role in admin/teacher |
| students | PATCH | `/students/{student_id}/schedule/{slot_id}` | role in admin/teacher |
| tasks | POST | `/tasks` | role in admin/teacher |
| tasks | GET | `/tasks/feed` | role in admin/teacher |
| tasks | GET | `/tasks/list` | role in admin/teacher |
| tasks | GET | `/tasks/projects` | role in admin/teacher |
| tasks | POST | `/tasks/projects` | role in admin/teacher |
| tasks | DELETE | `/tasks/projects/{project_id}` | role in admin/teacher |
| tasks | PATCH | `/tasks/projects/{project_id}` | role in admin/teacher |
| tasks | DELETE | `/tasks/{task_id}` | role in admin/teacher |
| tasks | PATCH | `/tasks/{task_id}` | role in admin/teacher |
| tasks | POST | `/tasks/{task_id}/done` | role in admin/teacher |
| tasks | POST | `/tasks/{task_id}/move` | role in admin/teacher |
| tasks | POST | `/tasks/{task_id}/skip` | role in admin/teacher |
| upcoming | GET | `/schedule/upcoming` | any CRM user |
