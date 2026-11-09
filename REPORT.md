# REPORT — ученический сайт itdeti, ночь 1 (вход ученика и кабинет)

Дата: 2026-11-10. Все изменения в ветках `lms-mvp`. Ничего не отправлено (`git push` не выполнялся), merge и деплой не делались.

| Репозиторий | Где | Коммиты в `lms-mvp` |
|---|---|---|
| `den1chis/itdeti` (бэкенд) | `itdeti/` | 6 коммитов поверх `e855cb6` |
| `den1chis/itdetiWeb` (CRM) | `itdetiWeb/` | 1 коммит поверх `52c869f` |
| `itdetiLearn` (новый ученический сайт) | `itdetiLearn/` | локальный git-репозиторий, ветка `lms-mvp`, 1 коммит, **без remote** |

Копия этого отчёта лежит в `itdeti/REPORT.md`.

## 1. Что сделано

### Задача 0. Подготовка — сделано
- Структура изучена (FastAPI, 11 роутеров, модели без `relationship()`, CRM — `app.js` + изолированные модули `lms.js`, `tasks.js`).
- `itdeti/.env` создан: `DATABASE_URL` из `dev.env` (схема `postgresql+asyncpg://`), новый `JWT_SECRET_KEY` (`secrets.token_urlsafe(64)`), `ANTHROPIC_API_KEY` пустой, `DISABLE_SCHEDULER=true`. `.env` в `.gitignore`, не закоммичен.
- `APP_ENV=dev` (а не `development`): при `development` сервер на старте выполняет `Base.metadata.create_all`, то есть сам создаёт таблицы без миграций и без RLS — это нарушало бы правила 3–5.
- `DISABLE_SCHEDULER`: в коде **не нашлось ни одной фоновой задачи** (напоминания и разбор уведомлений срабатывают от HTTP-запросов). Добавлена единая точка `core/background.py` (`register_job` / `start_background_jobs`), которая при `DISABLE_SCHEDULER=true` ничего не запускает; любые будущие задачи нужно регистрировать там.
- Сервер стартует на dev-базе, `/docs` → 200, `/health` → `{"status":"ok","env":"dev"}`.
- `scripts/seed_dev.py`: админ, 3 ученика с доступом, курс «Python (dev seed)» (2 модуля × 3 урока, материалы-ссылки и тексты, задания `practice` и `code` с `tests`), записи на курс, немного прогресса. Идемпотентен (повторный запуск ничего не дублирует, пароли учеников сбрасывает на известные). Учётки — в docstring скрипта (только dev).
- Защита от прода (`scripts/devguard.py`): скрипт запускается, только если в `DATABASE_URL` project ref dev-проекта `itdeti-dev` (`yctyhgbhqsbawgjaiock`, список зашит в код, чтобы правки одного `.env` не хватало) или localhost; при `APP_ENV=production` — отказ. Проверено: с чужим ref скрипт печатает `REFUSED…` и выходит с кодом 1.

### Задача 1. Миграция 001 — сделано, применена к dev
См. раздел 3.

### Задача 2. Бэкенд — сделано
**Вход ученика** — `POST /student/auth/login` (`routers/student_site.py`)
- bcrypt напрямую (`core/security.py`, без passlib). Логин без учёта регистра и пробелов.
- JWT ученика: только `role=student`, `student_id` и `exp`; срок 120 мин (`STUDENT_TOKEN_EXPIRE_MINUTES`), без refresh-токена.
- 5 неверных паролей → `locked_until = now + 15 мин` (ответ `423`, `detail.code=locked`, `retry_after_minutes`). Пока заблокировано, пароль даже не проверяется. Счётчик обновляется под `SELECT … FOR UPDATE` и коммитится до ответа (иначе `get_db` откатил бы его).
- Неизвестный логин и неверный пароль дают одинаковый ответ `401 invalid_credentials` (bcrypt по фиктивному хешу выравнивает время).
- Выключенный доступ или неактивный ученик → `403 disabled` (только после верного пароля).
- Ограничение частоты по IP: 10 попыток в минуту на маршрут входа (`core/rate_limit.py`, в памяти) → `429` + `Retry-After`. Ограничения — в разделе 4.

**Управление доступом (только `admin`)** — `routers/student_access.py`
- `GET /students/{id}/access` — состояние (без пароля).
- `GET /students/{id}/access/login-suggestion` — логин = первое слово имени латиницей (русские и казахские буквы) + 2–3 цифры из `secrets`, уникальный.
- `POST /students/{id}/access` (`{"login": null | "свой"}`) — создать; пароль 6 цифр через `secrets.choice`, возвращается **один раз**, в базе только bcrypt-хеш; `Cache-Control: no-store`.
- `POST /students/{id}/access/reset-password` — новый пароль (тоже один раз), снимает блокировку.
- `PATCH /students/{id}/access` (`login`, `is_active`) — правка логина (формат `[a-z0-9_.-]{3,32}`, уникальность → 409) и включение/выключение.

**Кабинет ученика** — `student_id` берётся только из токена (`core/student_auth.get_current_student`); при каждом запросе проверяется, что доступ и ученик активны, поэтому «Выключить доступ» действует сразу.
- `GET /student/me` — имя.
- `GET /student/courses` — курсы через `lms_enrollments` с числом уроков и пройденных.
- `GET /student/courses/{id}` — модули и уроки со статусами; если ученик не записан → `404` (не раскрываем, что курс существует).
- `GET /student/lessons/{id}` — материалы (с `embed_url` для Google-ссылок), задания **без `tests`** (в схеме `StudentTask` этого поля нет вообще), предыдущий/следующий урок. Чужой урок → `404`.
- `POST /student/lessons/{id}/progress` — `in_progress` / `done`. Запрос `in_progress` не понижает уже пройденный урок (`done` остаётся; вернуть может преподаватель в CRM).

**Изоляция ролей**
- `core/dependencies.get_current_user` теперь отклоняет токены с `role=student` или без `sub` (`401`). Раньше токен ученика на любом CRM-маршруте вызвал бы `KeyError` → `500`.
- Проверено тестом по **всем** маршрутам приложения (кроме `/auth/login|refresh|logout`, `/health` и `/student/*`): каждый отвечает 401/403 на токен ученика. `/auth/refresh` не превращает токен ученика в CRM-токен. CRM-токен не открывает `/student/*`.
- CORS: добавлены `http://localhost:5500`, `http://127.0.0.1:5500` и настройка `CORS_EXTRA_ORIGINS`. Сейчас в списке остаётся `"*"` (как было) — см. раздел 4.
- Новые роутеры подключены в `main.py` в отдельном `try/except`, как LMS: сбой их загрузки не роняет остальной API.

### Задача 3. Ученический сайт `itdetiLearn` — сделано
- Чистый HTML + JS (`index.html`, `app.js`, `style.css`), адрес API только в `config.js` (по умолчанию `http://localhost:8000`).
- Страницы на hash-маршрутах (подходят для GitHub Pages): вход → «Мои курсы» (прогресс-бар) → курс (модули, кружки с номерами, ✓ у пройденных, «Начат») → урок (материалы: «Открыть ↗» и «Показать здесь» для Google-ссылок, тексты, задания с подсказкой) → «✓ Урок пройден» → «Следующий урок →». При первом открытии урок помечается «Начат».
- Сообщения: «Неверный логин или пароль», «Вход заблокирован на N минут…», «Слишком много попыток…», «Доступ выключен…», «Нет связи с сервером», «Время входа закончилось» (истёк токен → экран входа). Кнопка «Выйти».
- Крупный шрифт (18–22 px), кнопки 48–60 px, вёрстка под телефон в первую очередь. Все данные с сервера выводятся через `textContent`, ссылки — только `http(s)`.
- Проверено в браузере (375×812): неверный пароль, вход, курсы, курс, урок, «Урок пройден».

### Задача 4. CRM — сделано
- Новый изолированный модуль `itdetiWeb/student-access.js` (подключён одной строкой в `app.html`), оборачивает `openStudentEditor` и добавляет в карточку ученика блок «Доступ к ученическому сайту»: предложенный логин (можно править) + «Создать доступ»; затем статус, последний вход, блокировка, правка логина, «Сбросить пароль», «Выключить/Включить доступ». Логин и пароль показываются один раз в жёлтой плашке («Скопировать», «Скрыть»). Для не-admin блок не показывается.
- Для локальной проверки в `app.js` и `index.html` добавлен необязательный адрес API: `localStorage.setItem("itdeti_api_override", "http://localhost:8000")` в консоли. Без этой записи CRM, как и раньше, ходит на `https://itdeti.onrender.com`.
- Версия `app.js?v=` в `app.html` обновлена (по правилу из комментария в `app.html`).
- Проверено в браузере: блок отображается, сброс пароля с подтверждением показывает новый пароль.

### Задача 5. Тесты — сделано, 57 passed
- `tests/test_student_units.py` (без базы): транслитерация и формат логина, пароль 6 цифр, ограничитель частоты, защита dev-базы, состав токена ученика.
- `tests/test_student_site.py` (на dev-базе, создаёт свои уникальные данные и удаляет их в конце):
  успешный вход; неверный пароль и несуществующий логин; блокировка на 5-й попытке и `423` даже с верным паролем; `429` на 11-й попытке с одного IP; ученик A не получает курс/урок/прогресс ученика B и курса, на который не записан; `student_id` в query игнорируется; прогресс и «done не откатывается»; `tests` и их содержимое не попадают в ответ; токен ученика отклоняется всеми CRM-маршрутами; CRM-токен не открывает кабинет; создание доступа (пароль один раз, в базе хеш, 409/400 на логины), сброс пароля, выключение (старый токен сразу перестаёт работать).
- Старые 41 тест тоже проходят. Время прогона ~2,5 мин (каждый запрос идёт в Supabase в Сеуле).

## 2. Как запустить локально

Бэкенд (`itdeti/`, Windows, Python 3.13):
```bash
cd itdeti
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install fastapi==0.111.0 "uvicorn[standard]==0.29.0" "sqlalchemy[asyncio]==2.0.36" asyncpg==0.30.0 pydantic==2.9.2 pydantic-settings==2.2.1 "python-jose[cryptography]==3.3.0" bcrypt==4.1.3 python-multipart==0.0.9 httpx==0.27.0 anthropic==0.26.1 python-dotenv==1.0.1 alembic==1.13.1 email-validator
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python scripts/apply_migration.py migrations/001_student_accounts.sql   # уже применена к dev; повторный запуск безопасен
.venv/Scripts/python scripts/seed_dev.py
.venv/Scripts/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
(`pip install -r requirements.txt` на Python 3.13 не работает — см. раздел 4. Рекомендую `PYTHONDONTWRITEBYTECODE=1`, иначе запуск меняет закоммиченные `__pycache__`.)

Тесты: `.venv/Scripts/python -m pytest -q -p no:cacheprovider`

Ученический сайт:
```bash
cd itdetiLearn
python -m http.server 5500
```
→ http://localhost:5500, тестовые ученики `aliya11`, `timur22`, `egor33` (пароли — в `scripts/seed_dev.py`).

CRM локально: `cd itdetiWeb && python -m http.server 5501` → http://127.0.0.1:5501, в консоли браузера `localStorage.setItem("itdeti_api_override","http://localhost:8000")`, обновить страницу, войти под админом из `seed_dev.py`. Убрать: `localStorage.removeItem("itdeti_api_override")`.

## 3. Миграции

| Файл | Что делает | Где применена |
|---|---|---|
| `itdeti/migrations/001_student_accounts.sql` | 1) таблица `student_accounts` (`id`, `student_id` FK→`students` UNIQUE ON DELETE CASCADE, `login` UNIQUE + CHECK формата, `password_hash`, `failed_attempts` int default 0, `locked_until`, `last_login_at`, `is_active` default true, `created_at`) + `ENABLE ROW LEVEL SECURITY`; 2) `students.teacher_id` UUID NULL FK→`users(id)` ON DELETE SET NULL + индекс; 3) `ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'methodist'` — **вне транзакции**, после `COMMIT`. Всё идемпотентно (`IF NOT EXISTS`), только добавления. | dev (`itdeti-dev`), 2026-11-10. **Не применена к проду.** |

Применение — `scripts/apply_migration.py` (с той же защитой от не-dev базы) или SQL-редактор Supabase. Политик RLS нет, как и у остальных таблиц: сервер ходит ролью `postgres` (bypass RLS), а anon/authenticated через PostgREST не видят ничего.

Перед деплоем ветки на прод миграцию нужно применить к прод-базе, иначе маршруты `/student/*` и `/students/{id}/access` будут падать (остальной API не затронут). `teacher_id` намеренно **не** добавлен в модель `Student`, чтобы прод-запросы к `students` не зависели от этой миграции.

## 4. Открытые вопросы, ограничения, найденные проблемы

### Противоречия заметок `context/` и ТЗ (сделано по ТЗ)
1. Маршруты: в заметках `POST /student-auth/login`, `GET /me/course`, `PATCH /me/lessons/{id}/progress`; по ТЗ — `/student/auth/login`, `/student/courses…`, `POST /student/lessons/{id}/progress`.
2. Вход: в заметках «вход один: сервер определяет роль и отправляет в кабинет или CRM»; по ТЗ — отдельный вход ученика (`student_accounts`, отдельный токен). Общая страница входа не делалась.
3. Кто выдаёт доступ: в заметках «создаёт преподаватель», по ТЗ — «для admin». Маршруты доступа — только `admin`.
4. Папка миграций: в проекте миграции лежат в `supabase/migrations/` с номерами до `011`; ТЗ требует `migrations/001_…`. Сделано по ТЗ, но теперь в репозитории два `001_*` в разных папках — стоит решить, где жить миграциям дальше.
5. Версия Python: ТЗ говорит Python 3.13, `Dockerfile` и `README` — 3.12.
6. Заметки описывают «один курс на ученика»; `/student/courses` по ТЗ возвращает список (работает и с одним курсом).
7. «Модель данных» говорит, что у `lms_enrollments` есть UNIQUE(ученик, курс) и каскадное удаление — в dev-базе этого нет (см. ниже).

### Ограничения сделанного
- **Ограничение частоты по IP — в памяти одного процесса**: сбрасывается при перезапуске, не делится между воркерами/инстансами. **Важно для Render:** за прокси `request.client.host` — адрес прокси, то есть все ученики попадут в одну «корзину» (10 входов в минуту на всю школу). Перед деплоем нужен запуск `uvicorn … --proxy-headers --forwarded-allow-ips='*'` (в `Dockerfile` сейчас этого нет; я его не менял) или ограничитель на уровне прокси/Redis.
- После сброса пароля уже выданные токены ученика живут до истечения (≤ 2 ч). Выключение доступа действует сразу. Для мгновенного отзыва нужна колонка вроде `password_changed_at` (новая миграция).
- Ответ `423 locked` раскрывает, что такой логин существует (ТЗ требует показывать «заблокирован на N минут»).
- Роль `methodist` есть в enum и в модели `User`, но прав под неё не настроено (CRM-маршруты с `teacher_or_admin` её не пускают, GET-маршруты с `get_current_user` пускают).
- Ученический сайт медленный локально (1–3 с на экран): каждый запрос делает несколько последовательных обращений к Supabase в Сеуле; `/student/courses` считает прогресс по курсу отдельными запросами. Для одного курса на ученика не критично.
- Для GitHub Pages: поменять `API_URL` в `itdetiLearn/config.js` и добавить адрес сайта в `CORS_EXTRA_ORIGINS`. У `itdetiLearn` нет удалённого репозитория — его нужно создать.

### Проблемы в существующем коде (только описаны, не исправлялись)
1. **Права на чтение:** многие GET-маршруты (`/students`, `/students/{id}/balance`, `/finance/summary`, `/finance/transactions`, `/finance/payments`, `/notifications`, `/schedule/*`, `/parents` и др.) проверяют только `get_current_user`, без роли. Любой пользователь из `users` с ролью `parent` (или будущий `teacher`/`methodist`) видит финансы и всех учеников. (Пользователей с ролью `student` из таблицы `users` моя правка `get_current_user` теперь не пускает.)
2. **CORS** `allow_origins=["*"]` вместе с `allow_credentials=True` — фактически разрешены любые сайты. Лучше явный список (CRM на GitHub Pages, ученический сайт, localhost).
3. **`APP_ENV=development` → `create_all` на старте**: неявные DDL без RLS в обход миграций; там же `echo=True` пишет все SQL-запросы с параметрами в лог.
4. **`requirements.txt` не ставится на Python 3.13**: `pydantic 2.7.1` (pydantic-core без колёс 3.13, сборка через Rust падает), `asyncpg 0.29.0` (то же), `SQLAlchemy 2.0.30` (ставится, но падает при импорте на 3.13). Локально использованы `pydantic 2.9.2`, `asyncpg 0.30.0`, `SQLAlchemy 2.0.36` — рецепт в `requirements-dev.txt`; `requirements.txt` не менялся (прод на Docker 3.12).
5. **Windows без `tzdata`**: `routers/finance.py` (`ZoneInfo("Asia/Almaty")`) падает при импорте — сервер не стартует. Нужен пакет `tzdata` (добавлен в `requirements-dev.txt`).
6. **`__pycache__/*.pyc` закоммичены** в репозиторий (несмотря на `.gitignore`); любой запуск Python их меняет. Есть даже `models/__pycache__/course.cpython-313.pyc` от несуществующего модуля.
7. **Расхождение схемы dev-базы с миграцией 008**: у всех внешних ключей `lms_*` правило `NO ACTION` вместо `ON DELETE CASCADE`, нет `UNIQUE (student_id, course_id)` у `lms_enrollments`, нет индексов по `course_id`/`module_id`/`lesson_id`; в `lms_enrollments` остались колонки `start_date`, `weekday`, `lesson_time` от удалённой миграции 009. Удаление курса/урока в dev может падать там, где на проде проходит. Похоже, dev-база создавалась не из миграций.
8. **Вход в CRM (`/auth/login`) без ограничения частоты и блокировки** — подбор пароля админа ничем не ограничен.
9. Refresh-токены CRM хранятся в базе открытым текстом; `/auth/logout` не требует авторизации (удаляет токен по значению — не опасно, но стоит знать).
10. `users.is_active` допускает `NULL`; `get_current_user` фильтрует `is_active == True`, так что пользователь с `NULL` не войдёт — неочевидно.
11. Адрес API в CRM зашит в двух местах (`app.js` и `index.html`); для локальной работы добавлен только необязательный override.

## 5. Что не сделано
- Всё, что в ТЗ «вне рамок» (сдача ДЗ, «Проверка ДЗ», Pyodide, «Смотреть как ученик», ограничения CRM для `teacher`, вход родителей, деплой).
- Миграция не применялась к проду; ветки не отправлены; `itdetiLearn` без remote.
- Внешний вид CRM-блока на телефоне не проверялся (проверен на десктопе); ученический сайт проверен на 375 px.
