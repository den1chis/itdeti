# REPORT — itdeti, ночь 3 (подготовка к проду)

Дата: 2026-11-10 → 2026-11-11. Работа в ветках `lms-mvp` (`itdeti`, `itdetiWeb`) и в локальном репозитории `itdetiLearn` (ветка `lms-mvp`, без remote).
`git push`, merge, `git remote add`, деплой и любые действия с прод-базой **не выполнялись**; все проверки SQL — только на dev-базе `itdeti-dev`, только чтением.
Копия отчёта: `itdeti/REPORT_NIGHT3.md`.

## 1. Итог в двух словах
- Все задачи ТЗ ночи 3 сделаны. Код готов к выкладке по инструкции `itdeti/docs/DEPLOY_RUNBOOK.md`.
- **Полный прогон тестов в конце ночи: 101 passed, 0 failed, 27 мин 06 с** (2026-11-11 00:00:32 → 00:27:40, dev-база). Это 78 прежних (57 ночи 1 + 21 ночи 2, ни один не менялся) + 23 новых.
- Блок «Доступ к ученическому сайту» переехал в «Курсы → Ученики → карточка ученика», с «Показать / Копировать / Скрыть» и «Снять блокировку»; проверен в браузере на 375 px и 1280 px.
- Ученический сайт готов к GitHub Pages: сам выбирает API, работает из подпапки, CSP, свои ключи хранилища (проверено в браузере).
- Миграции этой ночью не понадобились.

## 2. Коммиты
**`itdeti` (`lms-mvp`, поверх `c2c3fcd`), 10 коммитов (9 + этот отчёт):**
- `57da8a3` Untrack committed `__pycache__/*.pyc`; `.gitignore`: `*.pyc`, `*.log` (отдельный коммит п. 1.7: 18 файлов `.pyc` убраны из индекса)
- `7727d28` Staff: an admin cannot deactivate (409 `self_deactivation`) or demote (409 `self_demotion`) themselves
- `fe04903` `/auth/refresh`: refuse inactive users (401, no new tokens)
- `7a884ee` Docs only with `ENABLE_DOCS=true`; explicit CORS origins; `client_ip()` behind a proxy for the student login limiter
- `b009557` Tests night 3
- `e58d853` README: migrations applied manually in order, never run `001_reset_school_schema.sql`; `supabase/migrations/README.md`; `.env.example`
- `e19f6f9` `docs/ROUTES_ACCESS.md`: documentation routes only with `ENABLE_DOCS=true`
- `9ceb25b` Prod checks (SELECT only): `prod_preflight.sql`, `prod_postcheck.sql`, `prod_backup.sql`
- `8348267` `docs/DEPLOY_RUNBOOK.md`
- (последний) `REPORT_NIGHT3.md`

**`itdetiWeb` (`lms-mvp`, поверх `626fd14`), 3 коммита:**
- `a440401` Блок «Доступ к ученическому сайту» перенесён в «Курсы → Ученики → карточка» (`StudentAccess.mount`)
- `45eb1b9` `app.html`, `index.html`: новая версия `?v=2026111101` у всех подключений
- `5716644` `.gitignore`

**`itdetiLearn` (`lms-mvp`, поверх `229ff7f`, без remote), 3 коммита:**
- `7dd0fbc` `config.js`: адрес API по адресу сайта, переопределение `itdeti_learn_api_override`
- `84dbe21` Защита от XSS: CSP, только ключи `itdeti_learn_*`, iframe только Google с `sandbox`, стили через CSSOM
- `ca5ce68` `.nojekyll`, `.gitignore`, README

`__pycache__`/`.pyc`/`.env` не отслеживаются ни в одном из трёх репозиториев (проверено `git ls-files`).

## 3. Что сделано (по задачам ТЗ)

### 0. Подготовка
- `git status` во всех трёх папках был чистым; `.env` указывает на dev-проект `itdeti-dev` (`yctyhgbhqsbawgjaiock`).
- В dev `.env` добавлено `ENABLE_DOCS=true` (файл не коммитится).
- `docs/ROUTES_ACCESS.md` пересобран `scripts/routes_inventory.py`: маршрутов не прибавилось (138 строк), изменилась только пометка у `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, `/redoc` — «public, only with ENABLE_DOCS=true (404 otherwise)». Скрипт теперь сам включает `ENABLE_DOCS`, чтобы эти строки попадали в таблицу.

### 1. Бэкенд
1. **Админ не выключает сам себя** (`routers/staff.py`): `PATCH /staff/{свой id}` с `is_active=false` → 409 `{"code":"self_deactivation"}`; набор ролей без `admin` → 409 `self_demotion`. Проверка идёт до проверки «последний admin». Имя, телефон, добавление роли, `is_active=true` себе — можно. Другой admin выключить/понизить — как раньше.
2. **`/auth/refresh` проверяет `is_active`** (`routers/auth.py`): пользователь не найден → 401 (как было), `is_active` не `true` (в т. ч. `NULL`) → 401 `User is inactive`, новые токены не выдаются, старый refresh-токен не расходуется. Для активного admin — без изменений.
3. **Swagger выключен по умолчанию** (`main.py`, `core/config.py`): `/docs`, `/redoc`, `/openapi.json` есть только при `ENABLE_DOCS=true`; от `APP_ENV` не зависит.
4. **CORS: явный список** вместо `*`: `CORS_ORIGINS` (по умолчанию `https://den1chis.github.io`, `http://localhost:5500`, `http://127.0.0.1:5500`, `http://localhost:5501`, `http://127.0.0.1:5501`) + `CORS_EXTRA_ORIGINS`; `*` из списка выбрасывается, хвостовой `/` срезается.
   Авторизация CRM и ученического сайта — заголовок `Authorization` (в `app.js` нет `credentials`/cookie; проверено поиском по коду). В браузере: CRM с `localhost:5501` и сайт с `localhost:5500`/`127.0.0.1:5500` работают, preflight `OPTIONS` → 200.
   **Android WebView не проверен** (приложения у меня нет): если WebView грузит CRM с `https://den1chis.github.io`, всё работает; если с другого адреса (`file://`, свой хост) — браузерные запросы будут отклонены. Нативные запросы приложения (уведомления, виджет) CORS не касаются. Шаг проверки и обход — в runbook (шаг 9 п. 2, раздел 10).
5. **IP клиента за прокси** (`core/client_ip.py`): `BEHIND_PROXY=false` (по умолчанию) — адрес сокета, `X-Forwarded-For` игнорируется; `BEHIND_PROXY=true` — адрес `TRUSTED_PROXY_HOPS`-й справа в `X-Forwarded-For` (по умолчанию 1; несколько заголовков склеиваются; не IP / слишком короткий список → адрес сокета). Используется в ограничителе входа учеников. `--proxy-headers --forwarded-allow-ips='*'` не включался.
6. **README и опасная миграция**: из `README.md` убрано требование выполнять `001_reset_school_schema.sql`; раздел «Database → Migrations»: миграции в `supabase/migrations/`, вручную по порядку, `001_reset_school_schema.sql` на живой базе выполнять нельзя, `010` повторно не запускать. Новый `supabase/migrations/README.md` — предупреждение и список 001–013. Старые SQL-файлы не менялись.
7. **`__pycache__` из git** — отдельный коммит `57da8a3`; `.gitignore` в `itdeti` дополнен (`*.pyc`, `*.log`; `__pycache__/`, `.venv/`, `.env` уже были), в `itdetiWeb` и `itdetiLearn` созданы.
8. **`.env.example`**: `CORS_ORIGINS`, `ENABLE_DOCS`, `BEHIND_PROXY`, `TRUSTED_PROXY_HOPS` (без значений), `STUDENT_PASSWORD_KEY` (был); `APP_ENV=dev` вместо опасного `development`. Пустое значение (`ENABLE_DOCS=`) сервер понимает как «не задано» (безопасное значение по умолчанию), а не падает.

### 2. CRM (`itdetiWeb`)
- `student-access.js` переписан: больше не оборачивает `openStudentEditor`, а даёт `window.StudentAccess.mount(container, studentId)` и `clearSecrets()`. `lms.js` в карточке ученика оставляет пустое место `#itdlStudentAccess` (после курса, перед заметками) и вызывает `mount` в `try/catch`: нет модуля или ошибка — карточка работает без блока.
- Состав блока (только admin: роль из `/auth/me`; для остальных блок не рисуется): статус (включён/выключен, «вход заблокирован до …», последний вход, число неверных попыток), правка логина; если доступа нет — предложенный логин и «Создать доступ» (пароль сразу показывается); пароль скрыт `••••••`, «Показать» → `GET …/access/password`, рядом «Копировать» и «Скрыть»; «Сбросить пароль» (подтверждение, новый пароль показывается); «Снять блокировку» (видна при блокировке или ошибках > 0); «Выключить/Включить доступ». Ошибки: 409 `password_not_stored` → «Пароль недоступен для просмотра, сбросьте», 503 `password_view_disabled` → «Просмотр пароля отключён на сервере».
- Пароль выводится только через `textContent`, не попадает в `console`, URL, `localStorage`; «Скрыть», перерисовка карточки и переход на любую страницу CRM удаляют его из DOM.
- Стили — классы карточки `lms.js` (`card`, `itdl-*`, `btn`), нового дизайна нет. `?v=` обновлены у всех подключений в `app.html` и `index.html`.
- **Проверено в браузере** (локальный сервер + dev-база, admin из `seed_dev.py`): блок в «Курсы → Ученики → карточка»; «Показать» (6 цифр) → «Скрыть» (элемента нет в DOM); пароль открыт → переход на Главную → элемента нет; 5 неверных входов ученика → «заблокирован до …» + «Снять блокировку» → после снятия вход с верным паролем 200; «Сбросить пароль» → новый пароль входит (200), старый — 401; временный ученик: «Создать доступ» → вход 200, правка логина, выключить/включить; старая карточка (вкладка «Ученики») — блока нет; 375 px — без горизонтальной прокрутки, кнопки переносятся; 1280 px — норма. Временный ученик удалён из dev-базы, `seed_dev.py` перезапущен (пароли тестовых учеников снова как в его описании).
- Не проверены в браузере: сообщения 409/503 (на dev у всех учеников пароль сохранён и ключ есть; серверная часть покрыта тестами ночи 2).

### 3. Ученический сайт (`itdetiLearn`)
- `config.js`: `localhost`/`127.0.0.1` → `http://localhost:8000`, иначе `https://itdeti.onrender.com`; переопределение `localStorage['itdeti_learn_api_override']` (только `http(s)://хост[:порт]`).
- Пути относительные, hash-маршруты; `.nojekyll`. **Проверено**: копия сайта в подпапке `/itdetiLearn/` на `127.0.0.1:5500` — стили и скрипты грузятся, неверный пароль → «Неверный логин или пароль…», вход → «Мои курсы».
- XSS: `innerHTML` с данными нет (было и осталось — `textContent`); ссылки только `http(s)` с `rel="noopener noreferrer"`; встраивание только `https://docs.google.com` / `https://drive.google.com`, iframe с `sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-presentation"` и `referrerpolicy="no-referrer"`.
- CSP (`<meta>`): `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' http://localhost:8000 http://127.0.0.1:8000 https://itdeti.onrender.com; frame-src https://docs.google.com https://drive.google.com; manifest-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'`.
  **Что пришлось поправить, чтобы CSP не ломал работу:** полосы прогресса и отступы задавались атрибутом `style` — CSP без `'unsafe-inline'` их блокирует; теперь стили ставятся через `el.style.cssText` (CSSOM), CSP не ослаблялся. Убран лишний `allowfullscreen` (предупреждение браузера; полноэкранный режим даёт `allow="fullscreen"`).
- **Проверено в браузере (375 px)**: вход, «Мои курсы», курс, урок, «Показать здесь» с Google-ссылкой (временный материал с несуществующим ID: фрейм Google загрузился и показал «Файл не обнаружен» — значит, CSP и sandbox пропускают; временный материал удалён), «✓ Урок пройден» → «Следующий урок →», в консоли нет ошибок CSP. **Не проверено**: встраивание настоящей презентации (в dev-базе нет Google-материалов).
- Хранилище: сайт читает и пишет только `itdeti_learn_*` (функция `store` отказывает для любых других ключей). Проверено: при выходе с сайта подложенные ключи CRM `itdeti_access_token`/`itdeti_refresh_token` остались нетронутыми.
- README: GitHub Pages (Settings → Pages → `main`, корень), свой домен (`CNAME`, адрес API менять не нужно, домен добавить в `CORS_EXTRA_ORIGINS`), выбор API, безопасность.
- Репозиторий: `.gitignore`, ветка `lms-mvp` закоммичена, remote нет.

### 4. Прод-проверка и инструкция (только документы, к проду ничего не выполнялось)
- `scripts/prod_preflight.sql` — 12 SELECT с нормой в комментарии: число таблиц, RLS (все / без RLS), `user_role`, наличие `users.roles`, `students.teacher_id`, `lessons.teacher_id`, `student_accounts`, структура и ограничения `lms_enrollments`, правила удаления FK и индексы `lms_*` (сверка с 008), число строк основных таблиц, пользователи по ролям.
- `scripts/prod_postcheck.sql` — 9 SELECT после 012/013: новые колонки и таблица, `methodist`, `users.roles` заполнено у всех, RLS на `student_accounts`, FK и индексы, пустые новые колонки, число строк.
- `scripts/prod_backup.sql` — выгрузка всех таблиц в JSON двумя SELECT (первый пишет текст второго).
- **Все три файла прогнаны на dev-базе в транзакциях READ ONLY** (синтаксис и смысл проверены). Результаты на dev: 33 таблицы, RLS у всех; `user_role` = admin, teacher, parent, student, methodist; у `lms_enrollments` лишние колонки `start_date, weekday, lesson_time`, нет UNIQUE (student_id, course_id); все FK `lms_*` — `NO ACTION` вместо `CASCADE`; из 8 индексов 008 нет ни одного (только первичные ключи). Бэкап: 33 строки, JSON каждой таблицы разбирается, число записей совпадает с `count(*)`.
- `docs/DEPLOY_RUNBOOK.md`: бэкап → preflight с разбором → 012, 013, postcheck → ключ → переменные Render (включая `APP_ENV=production`, пустой Start Command) → слияние `itdeti` (GitHub и команды) и проверки `/health`, `/docs` = 404, вход admin, `/auth/me` → `itdetiWeb` и жёсткое обновление → новый репозиторий `itdetiLearn` и Pages → smoke-проверки (CRM, Android, «Тест Тестов», проверка ограничителя за прокси командой `curl`) → откат → засыпание Render (15 минут, по Render FAQ, проверено поиском 2026-11-10; варианты; регион West US).

### 5. Тесты
- Новый файл `tests/test_night3.py`, 23 теста:
  - `client_ip`: без прокси заголовок игнорируется; за прокси — крайний справа (и N-й справа при `TRUSTED_PROXY_HOPS=2`, несколько заголовков, IPv6); откат на адрес сокета; чтение настроек;
  - безопасные значения по умолчанию; пустые значения переменных = по умолчанию; разбор `CORS_ORIGINS`, `*` выбрасывается;
  - документация: без `ENABLE_DOCS` и с `false` → `/docs`, `/redoc`, `/openapi.json` = 404 (`/health` 200), с `true` → 200 (отдельный процесс, `.env` не читается);
  - CORS: preflight с `https://den1chis.github.io`, `localhost:5500`, `127.0.0.1:5501` → 200 и `Access-Control-Allow-Origin`; с `https://evil.example`, `https://den1chis.github.io.evil.example`, `null` → без CORS-заголовков;
  - (dev-база) себя выключить/лишить admin → 409 `self_deactivation`/`self_demotion`, остальное на себе можно; другого admin — можно; refresh активного → 200; выключенного (в т. ч. `is_active = NULL`) → 401 и новых токенов нет, после включения тот же refresh-токен работает; refresh несуществующего пользователя → 401;
  - (dev-база) ограничитель: при `BEHIND_PROXY=true` 10 входов с разными подделанными первыми адресами и одним правым → 11-й 429, другой правый адрес — своя корзина; при `BEHIND_PROXY=false` разные `X-Forwarded-For` не помогают → 11-й 429.
- Прежние тесты не менялись.
- **Полный прогон в конце ночи: `101 passed, 0 failed` за 1626 с (27 мин 06 с)**; предупреждения (983) — устаревшие API pydantic/anyio в библиотеках, не ошибки. Тесты удалили свои данные.

## 4. Что проверить утром (15 минут)
Учётки dev — в описании `itdeti/scripts/seed_dev.py` (admin и ученики; только dev).
1. **Запуск** (3 окна терминала, папка `D:\itdeti_learn`):
   ```bash
   cd itdeti && .venv/Scripts/python -B -m uvicorn main:app --host 127.0.0.1 --port 8000
   ```
   ```bash
   cd itdetiWeb && python -m http.server 5501
   ```
   ```bash
   cd itdetiLearn && python -m http.server 5500
   ```
2. **`/docs`**: http://localhost:8000/docs открывается (в dev `.env` `ENABLE_DOCS=true`). Остановить сервер, в `itdeti/.env` поставить `ENABLE_DOCS=false`, запустить → http://localhost:8000/docs даёт `{"detail":"Not Found"}`. Вернуть `true`.
3. **CRM**: http://localhost:5501, в консоли (F12) `localStorage.setItem("itdeti_api_override","http://localhost:8000")`, обновить, войти admin. Ученики → открыть любого — в форме **нет** блока доступа.
4. **Курсы → Ученики → Алия**: внизу «Доступ к ученическому сайту»: «Показать» → 6 цифр → «Копировать» → «Скрыть». «Сбросить пароль» → подтвердить → новый пароль виден (запомнить).
5. **Ученический сайт с телефона** (или F12 → режим телефона 375 px): http://localhost:5500 → 5 раз неверный пароль Алии → «Вход заблокирован на 15 минут».
6. В CRM обновить карточку Алии → «вход заблокирован до …» → «Снять блокировку» → на сайте вход с новым паролем → курс → урок → «✓ Урок пройден».
7. **Создать доступ**: Ученики → создать ученика «Проверка» → Курсы → Ученики → «Проверка» → «Создать доступ» → пароль показан → войти им на сайте. Потом удалить ученика.
8. После проверки: `itdeti/scripts/seed_dev.py` вернёт пароль Алии к исходному.

## 5. Переменные для Render
| Имя | Значение | Зачем |
|---|---|---|
| `STUDENT_PASSWORD_KEY` | **сгенерировать** новый (команда в runbook, шаг 4), копия — в менеджер паролей | просмотр паролей учеников |
| `BEHIND_PROXY` | `true` | лимит входа учеников по реальному IP, а не по адресу прокси |
| `TRUSTED_PROXY_HOPS` | `1`, затем проверить (runbook, шаг 9 п. 4); при общей «корзине» — `2` | сколько прокси дописывают адрес в `X-Forwarded-For` |
| `APP_ENV` | `production` (с части 2 это и значение по умолчанию) | ни в коем случае не `development` (иначе `create_all` на старте) |
| `ENABLE_DOCS` | **не задавать** | `/docs`, `/openapi.json` закрыты (404) |
| `CORS_ORIGINS` | не задавать (значение по умолчанию подходит) | разрешённые сайты |
| `CORS_EXTRA_ORIGINS` | не задавать; позже — свой домен ученического сайта | добавочные сайты |
| Start Command в Settings | пусто (берётся `Dockerfile`) | без `--forwarded-allow-ips='*'` |

## 6. Решения, принятые мной
1. Проверки «себя нельзя» идут раньше «последнего admin», поэтому на себя всегда отвечает `self_*`. Добавить себе роль (`["admin","teacher"]`) можно — это не понижение.
2. `/auth/refresh` считает `is_active = NULL` неактивным (как `get_current_user`); отказанный refresh-токен не удаляется — после включения пользователя он снова работает.
3. Пустая переменная (`ENABLE_DOCS=`, `TRUSTED_PROXY_HOPS=`) = «не задана» (иначе копия `.env.example` роняла бы сервер на старте).
4. `TRUSTED_PROXY_HOPS=0` трактуется как 1; не-IP в нужной позиции → адрес сокета.
5. Блок доступа: admin определяется через `/auth/me` (`role == "admin"`), так как в CRM роль нигде не хранится; кнопка «Снять блокировку» видна и при ошибках входа без блокировки (ТЗ: «когда есть блокировка или счётчик ошибок»); после «Создать»/«Сбросить» пароль сразу открыт.
6. Пароль убирается из DOM при любом переходе между страницами CRM (обёртка `loadPage` в `lms.js`) и при перерисовке карточки.
7. CSP `connect-src` — фиксированный список (локальный API и Render); переопределение API на другой адрес потребует правки `index.html` (так безопаснее, записано в README сайта).
8. Встраивание на ученическом сайте разрешено только для `docs.google.com` / `drive.google.com` и на клиенте (как в CSP), не только на сервере.
9. Бэкап — свой двухшаговый SELECT (`prod_backup.sql`), см. противоречие 1.
10. `.env.example`: `APP_ENV=dev` вместо `development`.

## 7. Вопросы владельцу
1. **IP клиента на Render.** По документации Render весь трафик идёт через Cloudflare, который дописывает адрес клиента в `X-Forwarded-For`; дописывает ли адрес ещё и балансировщик Render — не сказано. Поэтому `TRUSTED_PROXY_HOPS` нужно подтвердить проверкой после выкладки (runbook, шаг 9 п. 4). Render рекомендует также заголовок `CF-Connecting-IP` (его ставит Cloudflare и перезаписывает) — сделать поддержку следующей ночью?
2. **С какого адреса Android WebView грузит CRM?** Если не с `https://den1chis.github.io` — назовите адрес, добавлю в список CORS.
3. **Платный тариф Render** (без засыпания) — нужен ли к тесту с учениками?
4. Расхождения прод-схемы `lms_*` с миграцией 008 (если preflight их покажет, как на dev): чинить отдельной миграцией (каскады, UNIQUE, индексы)?
5. ~~`APP_ENV` по умолчанию в коде — `development`~~ — сделано в части 2: по умолчанию `production`.

## 8. Противоречия ТЗ и заметок
1. ТЗ (п. 4.1): «SQL-запрос из заметок для выгрузки JSON» — в папке `context/` такого запроса нет. Написан и проверен на dev свой (`scripts/prod_backup.sql`).
2. «Вопросы перед ночью 3», ответ 3: сотрудники входят на ученический сайт тем же логином — по ТЗ это ночь 4 (вне рамок), не делалось.
3. «Модель данных»: «008 … RLS включён» — в файле 008 (и 011) `ENABLE ROW LEVEL SECURITY` нет; на dev RLS включён у всех таблиц (вероятно, вручную). Проверка на проде — запрос [2]/[3] preflight.
4. «Вопросы перед ночью 3», п. 9: «расписание отправляет запрос каждые 30 минут» — при засыпании Render через 15 минут это не держит сервер бодрым (отмечено в runbook).
5. ТЗ п. 1.5 требует брать крайний справа адрес; это верно, только если перед приложением ровно настолько прокси, сколько указано в `TRUSTED_PROXY_HOPS` — см. вопрос 1.

## 9. Найденные проблемы (только описаны)
1. **Общая origin `den1chis.github.io`**: CRM хранит свои токены в `localStorage`; любая XSS на ученическом сайте (или другом сайте на этом же адресе) дала бы доступ к токенам CRM. Сайт усилен (CSP, `textContent`, свои ключи), но полностью это решает отдельный домен/поддомен для ученического сайта.
2. CSP через `<meta>` не может задать `frame-ancestors` (защиту от встраивания сайта в чужую страницу); GitHub Pages не даёт ставить заголовки.
3. Dev-схема `lms_*` расходится с 008 (нет каскадов, UNIQUE, индексов; лишние колонки от 009) — проверено запросами preflight на dev.
4. ~~Значение по умолчанию `APP_ENV="development"`~~ — исправлено в части 2 (по умолчанию `production`).
5. ~~Версии `requirements.txt` отличались от версий тестов~~ — выровнены в части 2. Docker-образ не собирался (Docker на машине нет), см. часть 2.
6. Ограничитель входа учеников — в памяти одного процесса (сбрасывается при перезапуске/засыпании Render).
7. В `itdetiWeb/index.html` подпись «API: https://itdeti.onrender.com» статична и не меняется при локальном переопределении (только текст).
8. Остаются в силе из ночей 1–2: вход в CRM без ограничения частоты и блокировки (решено делать до первого преподавателя), refresh-токены CRM в базе открытым текстом, `423 locked` раскрывает существование логина ученика, побочные действия GET-маршрутов календаря.

## 10. Что не сделано / вне рамок
- Всё из «ВНЕ рамок» ТЗ: раздел «Сотрудники» и экраны преподавателя, общий вход и режим сотрудника на ученическом сайте, блокировка входа в CRM и журнал, ДЗ, Pyodide, ✎ методиста, дизайн, зарплата, действия с продом, `push`/`merge`.
- Не проверено: Android-приложение и WebView, настоящая Google-презентация во встраивании, сообщения 409/503 в CRM в браузере, сборка Docker-образа, поведение `X-Forwarded-For` на Render.
- Миграции к проду не применялись; репозиторий `itdetiLearn` на GitHub не создавался.

---

# Часть 2 (2026-11-11, 00:40–01:10)
Те же правила: только `lms-mvp` и dev-база, без push, merge и действий с продом.

## Коммиты (`itdeti`, `lms-mvp`, поверх `4dae4d0`) и этот раздел
- `fb31e6e` `requirements.txt`: версии, на которых идут тесты; явные `cryptography`, `email-validator`, `greenlet`, `tzdata`
- `6b16761` `APP_ENV` по умолчанию `production`; README, `.env.example`, runbook; тест
- `fb29f08` Dockerfile: порт из `$PORT`, `exec uvicorn`, без `--reload`; `.dockerignore`
- (последний) этот раздел отчёта

## 1. `requirements.txt`
- Сторонние импорты кода (разбор `ast` всех `.py`, кроме `.venv`): `asyncpg`, `bcrypt`, `cryptography`, `dotenv`, `fastapi`, `jose`, `pydantic`, `pydantic_settings`, `sqlalchemy`, `starlette`, `uvicorn` (+ `pytest` в тестах). Косвенно нужны: `email-validator` (`EmailStr` в схемах), `greenlet` (асинхронный SQLAlchemy), `tzdata` (`ZoneInfo("Asia/Almaty")`), `httpx` (TestClient, anthropic).
- Было → стало: `pydantic 2.7.1 → 2.9.2`, `SQLAlchemy 2.0.30 → 2.0.36`, `asyncpg 0.29.0 → 0.30.0`; добавлены явно `starlette==0.37.2`, `greenlet==3.5.6`, `email-validator==2.3.0`, `cryptography==50.0.2`, `tzdata==2026.6`. Без изменений: `fastapi 0.111.0`, `uvicorn[standard] 0.29.0`, `pydantic-settings 2.2.1`, `python-jose[cryptography] 3.3.0`, `bcrypt 4.1.3`, `python-multipart 0.0.9`, `httpx 0.27.0`, `python-dotenv 1.0.1`, `anthropic 0.26.1`, `alembic 1.13.1`. Все версии — ровно те, что стоят в `.venv`, на котором прошли тесты.
- `anthropic`, `alembic`, `python-multipart` кодом сейчас не импортируются; оставлены, как были.
- **Проверка для Python 3.12**: `pip install --dry-run --python-version 3.12 --platform manylinux…x86_64 --only-binary=:all: -r requirements.txt` — зависимости разрешились, все пакеты есть готовыми колёсами для CPython 3.12 Linux (сборка из исходников не нужна; например `asyncpg-0.30.0-cp312-…manylinux_2_17`, `pydantic_core-2.23.4-cp312-…manylinux_2_17`, `cryptography-50.0.2-cp311-abi3-manylinux_2_28`). Сама установка на 3.12 не выполнялась (Python 3.12 на машине нет).
- `requirements-dev.txt` теперь только `pytest`: обходной рецепт для 3.13 больше не нужен (`tzdata` переехал в `requirements.txt`).

## 2. Синтаксис как Python 3.12
`ast.parse(..., feature_version=(3, 12))` по всем `.py`: `itdeti` — 72 файла, `itdetiWeb/tools` — 2 файла; **ошибок 0**. (Проверка ловит синтаксис новее 3.12, но не использование функций стандартной библиотеки, появившихся в 3.13.)

## 3. `APP_ENV` по умолчанию `production`
- `core/config.py`: `APP_ENV: str = "production"`. Только явное значение `development` включает `create_all` на старте и вывод всех SQL в лог. Локальный `.env` по-прежнему `APP_ENV=dev` (проверено: `/health` → `"env":"dev"`).
- Dev-скрипты (`seed_dev.py`, `apply_migration.py`, `demo_roles.py`) при `APP_ENV=production` отказываются работать, как и раньше; без `APP_ENV` в `.env` они теперь тоже откажутся (безопаснее).
- README, `.env.example`, runbook (таблица переменных Render) обновлены; тест `test_app_env_defaults_to_production`.

## 4. Dockerfile
- Было: `python:3.12-slim`, без `--reload`, порт жёстко `8000`.
- Стало: тот же `python:3.12-slim`, без `--reload`, `CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]` (порт из `PORT`, который задаёт Render, иначе 8000; `exec` — uvicorn получает SIGTERM при передеплое), `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`. Без `--proxy-headers`.
- Новый `.dockerignore`: `.env`, `.venv/`, `.git/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.log` — при локальной сборке секреты и окружение не попадут в образ (Render собирает из git, где `.env` нет).
- **Docker на машине не установлен** (`docker: command not found`), образ не собирался и не запускался; устанавливать не стал.
- Замена проверки: та же команда запуска, что в `CMD`, локально (Python 3.13, dev-база):
  - `PORT=8011` → uvicorn слушает `8011`, `/health` → `{"status":"ok","env":"dev"}`, `/docs` → 200 (в dev `.env` `ENABLE_DOCS=true`), ошибок в логе нет;
  - `PORT=8012 APP_ENV=production ENABLE_DOCS=false` → `/health` → `{"status":"ok","env":"production"}`, `/docs`, `/redoc`, `/openapi.json` → 404.
  - Подстановка `${PORT:-8000}` в `sh`: без `PORT` → 8000, `PORT=10000` → 10000.
  Оба временных сервера остановлены.

## 5. Тесты и импорт
- Импорт приложения: `import main` — без ошибок; 138 маршрутов (134 API + 4 документации, т. к. в dev `ENABLE_DOCS=true`), неразмеченных маршрутов 0; CORS — 5 адресов по умолчанию.
- Быстрые тесты `pytest -k "not route_matrix"`: **101 passed, 1 deselected (матрица маршрутов), 0 failed, 746,9 с (12 мин 26 с)**, 2026-11-11 00:55:07 → 01:07:35, dev-база. Всего тестов теперь 102 (+1 тест `APP_ENV`). Матрица маршрутов в части 2 не перезапускалась (в последнем полном прогоне части 1 она прошла; маршруты в части 2 не менялись).

## Что осталось непроверенным после части 2
- Сборка и запуск Docker-образа (нет Docker) и установка `requirements.txt` на настоящем Python 3.12 — проверено только разрешение зависимостей. На Render это проверится при первом деплое (runbook, шаг 6: статус «Live» и `/health`); если сборка упадёт — Rollback (раздел 10).
