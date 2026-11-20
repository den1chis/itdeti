"""Deploy of night 6 to production (TZ_NIGHT6 section 6): the night 5 script with migration 016 (savings),
finance control sums before/after and the night 6 smoke test. Run by the agent itself in night 6.

    python scripts/deploy_night6.py --prod                 # all steps, in order
    python scripts/deploy_night6.py --prod --from-step 4   # continue after a stop
    python scripts/deploy_night6.py --prod --only-step 6   # one step (task 1 of night 6: CRM only, no migration)

Steps:
  1  prod_ops preflight + counts + finance control sums (saved to the backup folder)
  2  prod_ops backup
  3  prod_ops migrate 016_savings.sql, postcheck 016, counts and finance sums compared with step 1
  4  itdeti: push lms-mvp, merge --no-ff into main, push main, wait for the new /health version on Render
  5  server smoke test (night 4 + night 5 checks + night 6: the new finance routes closed without a token and,
     with a test teacher from prod.env, 403); on failure the server merge is reverted (the migration stays)
  6  itdetiWeb: push, merge, push main, wait for GitHub Pages (new ?v=), every referenced file of app.html,
     index.html and staff.html answers 200
  7  itdetiLearn: push and merge only when there are new commits; the site answers 200
  8  prod_ops counts and finance sums compared with step 1 again

Teacher checks of the smoke test need a TEST teacher on production: SMOKE_TEACHER_EMAIL and
SMOKE_TEACHER_PASSWORD in the secrets file prod.env (never printed). Without them those checks are skipped.

Every step stops the whole run on the first problem (exit code 1) and prints what to do. Nothing here prints
or stores the database URL: the database is touched only through scripts/prod_ops.py.
Logins on production use only addresses that cannot exist (nobody-check-...@example.com)."""
import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.version import APP_VERSION  # noqa: E402

API = "https://itdeti.onrender.com"
CRM = "https://den1chis.github.io/itdetiWeb"
LEARN = "https://den1chis.github.io/itdetiLearn"
ORIGIN_OK = "https://den1chis.github.io"
ORIGIN_BAD = "https://evil.example"
MIGRATION = "016_savings.sql"
MIGRATION_NUMBER = "016"
SECRETS_FILE = Path(r"D:\itdeti_secrets\prod.env")
BACKUP_DIR = Path(r"D:\itdeti_backups")
COUNTS_FILE = BACKUP_DIR / "counts_night6_before.json"
FINANCE_FILE = BACKUP_DIR / "finance_night6_before.json"
STATE_FILE = BACKUP_DIR / "deploy_night6_state.json"
MERGE_MESSAGE = {
    "itdeti": "Merge lms-mvp: night 6 (finance by period, forecast, savings accounts and movements)",
    "itdetiWeb": "Merge lms-mvp: night 6 (tasks week columns, new Финансы page with savings, new ?v=)",
    "itdetiLearn": "Merge lms-mvp: night 6",
}


NO_AUTO_REVERT = False


class Stop(Exception):
    pass


def say(text: str) -> None:
    print(text, flush=True)


def sh(args, cwd, check=True) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (result.stdout + result.stderr).strip()
    if check and result.returncode != 0:
        raise Stop(f"команда {' '.join(args)} в {Path(cwd).name} завершилась с кодом {result.returncode}:\n{out[-1500:]}")
    return out


def prod_ops(*args) -> None:
    code = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "prod_ops.py"), "--prod", *args], cwd=ROOT).returncode
    if code != 0:
        raise Stop(f"prod_ops {' '.join(args)}: код {code} (1 — не норма, 2 — отказ). Выкладка остановлена.")


def state(**update) -> dict:
    data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    if update:
        data.update(update)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


# ---------------------------------------------------------------- git
def merge_and_push(repo: str) -> str:
    """lms-mvp -> origin; main <- lms-mvp (--no-ff); push main. Returns the merge commit."""
    cwd = WORKSPACE / repo
    if sh(["git", "status", "--porcelain"], cwd):
        raise Stop(f"{repo}: рабочая папка не чистая (git status). Сначала закоммитить или убрать изменения.")
    if sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd) != "lms-mvp":
        raise Stop(f"{repo}: текущая ветка не lms-mvp")
    sh(["git", "fetch", "origin"], cwd)
    sh(["git", "push", "origin", "lms-mvp"], cwd)
    sh(["git", "checkout", "main"], cwd)
    try:
        sh(["git", "merge", "--ff-only", "origin/main"], cwd)
        before = sh(["git", "rev-parse", "HEAD"], cwd)
        if sh(["git", "rev-list", "--count", "main..lms-mvp"], cwd) == "0":
            say(f"  {repo}: в lms-mvp нет новых коммитов для main — слияние не нужно")
            return before
        merged = subprocess.run(["git", "merge", "--no-ff", "lms-mvp", "-m", MERGE_MESSAGE[repo]], cwd=cwd,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        if merged.returncode != 0:
            sh(["git", "merge", "--abort"], cwd, check=False)
            raise Stop(f"{repo}: конфликт слияния lms-mvp -> main, слияние отменено:\n{(merged.stdout + merged.stderr)[-1200:]}")
        commit = sh(["git", "rev-parse", "HEAD"], cwd)
        sh(["git", "push", "origin", "main"], cwd)
        say(f"  {repo}: main {before[:7]} -> {commit[:7]} (отправлено)")
        return commit
    finally:
        sh(["git", "checkout", "lms-mvp"], cwd, check=False)


def revert_server(merge_commits: list) -> None:
    """Revert every merge of this deploy (newest first) in main and push."""
    cwd = WORKSPACE / "itdeti"
    sh(["git", "fetch", "origin"], cwd)
    sh(["git", "checkout", "main"], cwd)
    try:
        sh(["git", "merge", "--ff-only", "origin/main"], cwd)
        for commit in reversed(merge_commits):
            say(f"ОТКАТ СЕРВЕРА: git revert -m 1 {commit[:7]} в main")
            sh(["git", "revert", "-m", "1", "--no-edit", commit], cwd)
        sh(["git", "push", "origin", "main"], cwd)
        state(server_reverted=sh(["git", "rev-parse", "HEAD"], cwd))
    finally:
        sh(["git", "checkout", "lms-mvp"], cwd, check=False)


# ---------------------------------------------------------------- http
def http() -> httpx.Client:
    return httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "itdeti-deploy-check"})


def wait_for_version(expected: str, minutes: int = 15, want_equal: bool = True) -> dict:
    deadline = time.time() + minutes * 60
    last = None
    with http() as client:
        while time.time() < deadline:
            try:
                last = client.get(f"{API}/health").json()
            except Exception as error:  # the service restarts while deploying
                last = {"error": type(error).__name__}
            if (last.get("version") == expected) == want_equal and "error" not in last:
                return last
            say(f"  /health: {last} — жду {'новую' if want_equal else 'старую'} версию…")
            time.sleep(30)
    raise Stop(f"за {minutes} минут /health не показал {'версию ' + expected if want_equal else 'старую версию'}; последний ответ: {last}")


def server_smoke() -> list:
    """Section 4 step 5. Returns the list of failed checks (empty = passed). A network error or a broken
    answer is a failed check too (it must lead to the rollback, not to a traceback)."""
    # The login checks use up the per-IP limit of this computer for a minute: a repeated smoke test within
    # that minute would fail falsely (and roll the server back). Wait it out.
    try:
        last = float(state().get("smoke_logins_at", 0))
    except Exception:
        last = 0.0
    if time.time() - last < 70:
        wait = int(70 - (time.time() - last)) + 1
        say(f"  жду {wait} с: предыдущий смоук занял лимит входа с этого IP")
        time.sleep(wait)
    try:
        return _server_smoke()
    except Exception as error:
        say(f"  FAIL смоук прерван ошибкой: {type(error).__name__}: {str(error)[:300]}")
        return [f"ошибка смоука: {type(error).__name__}"]
    finally:
        try:
            state(smoke_logins_at=time.time())  # the per-IP window starts after the last login attempt
        except Exception:
            pass


def _server_smoke() -> list:
    failures, run_id = [], uuid.uuid4().hex[:8]

    def check(name, ok, got):
        say(f"  {'ok  ' if ok else 'FAIL'} {name}: {got}")
        if not ok:
            failures.append(f"{name}: {got}")

    with http() as client:
        health = client.get(f"{API}/health").json()
        check("/health env=production и новая version", health.get("env") == "production" and health.get("version") == APP_VERSION, health)
        for path in ("/docs", "/openapi.json", "/redoc"):
            code = client.get(API + path).status_code
            check(f"{path} -> 404", code == 404, code)
        code = client.get(f"{API}/audit").status_code
        # Without an Authorization header FastAPI's HTTPBearer answers 403 on every CRM route (unchanged
        # behaviour); with a bad token it is 401. Both mean "no access" — the point is: not 200.
        check("GET /audit без токена -> 401/403 (без доступа)", code in (401, 403), code)
        code = client.get(f"{API}/audit", headers={"Authorization": "Bearer not-a-token"}).status_code
        check("GET /audit с неверным токеном -> 401", code == 401, code)

        # CORS: allowed origin gets the header, a foreign one does not.
        preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
        good = client.options(f"{API}/auth/login", headers={"Origin": ORIGIN_OK, **preflight})
        check("CORS для den1chis.github.io", good.headers.get("access-control-allow-origin") == ORIGIN_OK, good.headers.get("access-control-allow-origin"))
        bad = client.options(f"{API}/auth/login", headers={"Origin": ORIGIN_BAD, **preflight})
        check("CORS для evil.example отсутствует", "access-control-allow-origin" not in bad.headers, bad.headers.get("access-control-allow-origin"))

        # CRM login: an address that cannot exist; a new forged X-Forwarded-For every time.
        email = f"nobody-check-{run_id}@example.com"
        codes = []
        for i in range(11):
            r = client.post(f"{API}/auth/login", json={"email": email, "password": "not-a-password"},
                            headers={"X-Forwarded-For": f"203.0.113.{i + 1}"})
            codes.append(r.status_code)
        check("POST /auth/login: первый 401, 11-й 429", codes[0] == 401 and codes[10] == 429, codes)
        # The student login has its own per-IP limiter (10 a minute), independent of the CRM one above.
        codes = []
        for i in range(11):
            r = client.post(f"{API}/student/auth/login", json={"login": f"nobody-check-{run_id}", "password": "000000"},
                            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"})
            codes.append(r.status_code)
        student_ok = codes[:10] == [401] * 10 and codes[10] == 429
        say(f"  {'ok  ' if student_ok else 'FAIL'} POST /student/auth/login (п. 2.1): десять 401 и 429: {codes}")
        if not student_ok:
            # TZ 2.1: this part alone does not roll the deploy back; it goes to the report.
            say("  ВНИМАНИЕ: лимит входа учеников по IP на проде не сработал — записать в отчёт, выкладку не откатывать")
            state(student_limit_failed=codes)

        night5_smoke(client, check, run_id)
        night6_smoke(client, check)
    return failures


def smoke_secret(name: str) -> str:
    """Optional smoke-test value from the secrets file (never printed). Empty when absent."""
    try:
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def night5_smoke(client, check, run_id) -> None:
    """Night 5 checks. The per-IP login limit (10 a minute) is spent by the night 4 checks above: wait it out."""
    say("  --- ночь 5 ---")
    # Routes that must not open without a staff session.
    code = client.post(f"{API}/auth/handoff", json={"target": "catalog"}).status_code
    check("POST /auth/handoff без токена -> 401/403", code in (401, 403), code)
    code = client.post(f"{API}/auth/handoff", json={"target": "catalog"}, headers={"Authorization": "Bearer not-a-token"}).status_code
    check("POST /auth/handoff с неверным токеном -> 401", code == 401, code)
    code = client.get(f"{API}/staff/00000000-0000-0000-0000-000000000000/students").status_code
    check("GET /staff/{id}/students без токена -> 401/403", code in (401, 403), code)
    code = client.get(f"{API}/teacher/learning/students").status_code
    check("GET /teacher/learning/students без токена -> 401/403", code in (401, 403), code)
    r = client.post(f"{API}/auth/handoff/redeem", json={"code": "x" * 43}, headers={"Origin": ORIGIN_OK})
    check("POST /auth/handoff/redeem с выдуманным кодом -> 401 invalid_code",
          r.status_code == 401 and "invalid_code" in r.text, r.status_code)

    say("  жду 65 с: лимит входа с этого IP занят проверками ночи 4")
    time.sleep(65)
    # Shared lock: the platform posts to the same /auth/login (Origin den1chis.github.io). 5 wrong passwords
    # of one (email, IP) pair -> the 6th attempt is 429 for the CRM as well. An address that cannot exist.
    email = f"nobody-check-n5-{run_id}@example.com"
    codes = []
    for _ in range(6):
        codes.append(client.post(f"{API}/auth/login", json={"email": email, "password": "not-a-password"},
                                 headers={"Origin": ORIGIN_OK}).status_code)
    check("общая блокировка: 5 неверных с платформы, 6-й (как из CRM) -> 429", codes[:5] == [401] * 5 and codes[5] == 429, codes)

    teacher_email, teacher_password = smoke_secret("SMOKE_TEACHER_EMAIL"), smoke_secret("SMOKE_TEACHER_PASSWORD")
    if not (teacher_email and teacher_password):
        say("  ПРОПУЩЕНО: вход преподавателя, handoff, 403 на «Сотрудники» — нет SMOKE_TEACHER_EMAIL/PASSWORD в "
            "prod.env (проверить по чек-листу владельца)")
        state(teacher_smoke_skipped=True)
        return
    say("  жду 65 с: лимит входа с этого IP")
    time.sleep(65)
    r = client.post(f"{API}/auth/login", json={"email": teacher_email, "password": teacher_password}, headers={"Origin": ORIGIN_OK})
    check("вход тестового преподавателя (путь платформы) -> 200", r.status_code == 200, r.status_code)
    if r.status_code != 200:
        return
    tokens = r.json()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = client.get(f"{API}/auth/me", headers=auth).json()
    check("/auth/me: роль teacher, без admin", "teacher" in me.get("roles", []) and "admin" not in me.get("roles", []), me.get("roles"))
    for path in ("/staff", "/students", "/finance/payments", "/audit"):
        code = client.get(API + path, headers=auth).status_code
        check(f"преподаватель: GET {path} -> 403", code == 403, code)
    r = client.get(f"{API}/teacher/learning/students", headers=auth)
    money = {"balance", "lesson_price", "price", "amount", "payments"}
    check("преподаватель: /teacher/learning/students -> 200, без денег",
          r.status_code == 200 and not any(k in r.text for k in money), r.status_code)
    r = client.post(f"{API}/auth/handoff", json={"target": "catalog"}, headers=auth)
    check("handoff: выдача кода -> 201", r.status_code == 201, r.status_code)
    if r.status_code == 201:
        code_value = r.json().get("code", "")
        first = client.post(f"{API}/auth/handoff/redeem", json={"code": code_value}, headers={"Origin": ORIGIN_OK})
        check("handoff: обмен кода -> 200, новые токены", first.status_code == 200
              and first.json().get("access_token") != tokens["access_token"], first.status_code)
        again = client.post(f"{API}/auth/handoff/redeem", json={"code": code_value}, headers={"Origin": ORIGIN_OK})
        check("handoff: повтор того же кода -> 401", again.status_code == 401, again.status_code)
        if first.status_code == 200:
            client.post(f"{API}/auth/logout", json={"refresh_token": first.json()["refresh_token"]})
    client.post(f"{API}/auth/logout", json={"refresh_token": tokens["refresh_token"]})


NIGHT6_ROUTES = [
    ("GET", "/finance/period"), ("GET", "/finance/period/operations"), ("GET", "/finance/forecast"),
    ("GET", "/finance/savings"), ("POST", "/finance/savings/accounts"),
    ("PATCH", "/finance/savings/accounts/00000000-0000-0000-0000-000000000000"),
    ("DELETE", "/finance/savings/accounts/00000000-0000-0000-0000-000000000000"),
    ("GET", "/finance/savings/movements"), ("POST", "/finance/savings/movements"),
    ("PATCH", "/finance/savings/movements/00000000-0000-0000-0000-000000000000"),
    ("DELETE", "/finance/savings/movements/00000000-0000-0000-0000-000000000000"),
    ("GET", "/finance/savings/monthly"),
]


def night6_smoke(client, check) -> None:
    """Night 6: every new finance route is closed without a token and with a broken token (no login needed).
    With a TEST teacher from prod.env: 403 on the new routes. The old /finance/summary stays closed too."""
    say("  --- ночь 6 ---")
    for method, path in NIGHT6_ROUTES + [("GET", "/finance/summary"), ("GET", "/finance/transactions")]:
        code = client.request(method, API + path, json={}).status_code
        check(f"{method} {path} без токена -> 401/403", code in (401, 403), code)
        code = client.request(method, API + path, json={}, headers={"Authorization": "Bearer not-a-token"}).status_code
        check(f"{method} {path} с неверным токеном -> 401", code == 401, code)
    teacher_email, teacher_password = smoke_secret("SMOKE_TEACHER_EMAIL"), smoke_secret("SMOKE_TEACHER_PASSWORD")
    if not (teacher_email and teacher_password):
        say("  ПРОПУЩЕНО: 403 преподавателю на новых маршрутах финансов — нет SMOKE_TEACHER_EMAIL/PASSWORD в prod.env "
            "(покрыто тестами test_night6.py и матрицей доступа; проверить по чек-листу)")
        state(teacher_smoke_night6_skipped=True)
        return
    say("  жду 65 с: лимит входа с этого IP")
    time.sleep(65)
    r = client.post(f"{API}/auth/login", json={"email": teacher_email, "password": teacher_password}, headers={"Origin": ORIGIN_OK})
    check("ночь 6: вход тестового преподавателя -> 200", r.status_code == 200, r.status_code)
    if r.status_code != 200:
        return
    tokens = r.json()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    for method, path in NIGHT6_ROUTES:
        code = client.request(method, API + path, json={}, headers=auth).status_code
        check(f"преподаватель: {method} {path} -> 403", code == 403, code)
    client.post(f"{API}/auth/logout", json={"refresh_token": tokens["refresh_token"]})


def wait_for_pages(url: str, marker: str, minutes: int = 10) -> str:
    deadline = time.time() + minutes * 60
    with http() as client:
        while time.time() < deadline:
            r = client.get(url, params={"nocache": uuid.uuid4().hex}, headers={"Cache-Control": "no-cache"})
            if r.status_code == 200 and marker in r.text:
                return r.text
            say(f"  {url}: {r.status_code}, нового содержимого ещё нет — жду…")
            time.sleep(20)
    raise Stop(f"за {minutes} минут {url} не обновился (нет «{marker}»)")


def check_assets(base: str, html: str) -> None:
    refs = sorted(set(re.findall(r'(?:src|href)="([^":#]+\?v=\d+)"', html)))
    if not refs:
        raise Stop(f"{base}: в странице не найдено подключений с ?v=")
    with http() as client:
        for ref in refs:
            code = client.get(f"{base}/{ref}").status_code
            say(f"  {'ok  ' if code == 200 else 'FAIL'} {ref}: {code}")
            if code != 200:
                raise Stop(f"{base}/{ref} отвечает {code}")


def page_version(repo: str, page: str) -> str:
    text = (WORKSPACE / repo / page).read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=(\d+)", text))
    if len(versions) != 1:
        raise Stop(f"{repo}/{page}: ожидалось одно значение ?v=, найдено {sorted(versions)}")
    return versions.pop()


# ---------------------------------------------------------------- steps
def step1():
    prod_ops("preflight")
    prod_ops("counts", "--save", str(COUNTS_FILE))
    prod_ops("finance", "--save", str(FINANCE_FILE))


def step2():
    prod_ops("backup")


def step3():
    prod_ops("migrate", MIGRATION)
    prod_ops("postcheck", MIGRATION_NUMBER)
    prod_ops("counts", "--compare", str(COUNTS_FILE))
    prod_ops("finance", "--compare", str(FINANCE_FILE))


def step4():
    reverted = state().get("server_reverted")
    if reverted:
        raise Stop(f"сервер уже откатывался в этой выкладке (коммит отката {reverted[:7]} в main). Повторное слияние "
                   "не вернёт код ночи 6: сначала вручную `git revert <коммит отката>` в main (docs/RUNBOOK_EMERGENCY.md, "
                   "раздел 2), затем удалить ключ server_reverted из D:\\itdeti_backups\\deploy_night6_state.json")
    with http() as client:
        before = client.get(f"{API}/health").json()
    say(f"  сейчас на проде: {before}")
    state(health_before=before)
    head_before = sh(["git", "rev-parse", "origin/main"], WORKSPACE / "itdeti")
    commit = merge_and_push("itdeti")
    if commit != head_before:  # a real merge happened: remember it for the rollback (all merges of this deploy)
        state(server_merges=state().get("server_merges", []) + [commit])
    say(f"  жду деплой Render (до 15 минут): version = {APP_VERSION}")
    say(f"  новая версия отвечает: {wait_for_version(APP_VERSION)}")


def step5():
    failures = server_smoke()
    if failures:
        # One retry before a rollback: a single network hiccup on this computer must not undo a good release.
        say("  смоук не прошёл — повтор через минуту перед откатом: " + "; ".join(failures))
        failures = server_smoke()
    if not failures:
        say("  смоук сервера пройден")
        return
    merges = state().get("server_merges", [])
    if NO_AUTO_REVERT:
        raise Stop("смоук сервера не прошёл (--no-auto-revert: откат НЕ выполнялся; команды — docs/RUNBOOK_EMERGENCY.md, "
                   f"коммиты слияния {', '.join(c[:7] for c in merges) or 'неизвестны'}): " + "; ".join(failures))
    if not merges:
        raise Stop("смоук сервера не прошёл, а коммит слияния неизвестен — откат вручную (docs/RUNBOOK_EMERGENCY.md): " + "; ".join(failures))
    revert_server(merges)
    say(f"  старая версия отвечает: {wait_for_version(APP_VERSION, want_equal=False)}")
    raise Stop("смоук сервера не прошёл, сервер откачен (миграция оставлена): " + "; ".join(failures))


def step6():
    version = page_version("itdetiWeb", "app.html")
    state(crm_merge=merge_and_push("itdetiWeb"))
    html = wait_for_pages(f"{CRM}/app.html", f"?v={version}")
    if "staff-admin.js" not in html or "handoff.js" not in html:
        raise Stop("app.html на Pages без staff-admin.js / handoff.js")
    if state().get("server_merges") and "finance-v2.js" in html:  # full night 6 deploy: the new page must be there
        script = wait_for_pages(f"{CRM}/finance-v2.js?v={version}", "/finance/savings")
        if "/finance/period" not in script:
            raise Stop("finance-v2.js на Pages без новой страницы «Финансы»")
    check_assets(CRM, html)
    check_assets(CRM, wait_for_pages(f"{CRM}/index.html", f"?v={version}"))
    check_assets(CRM, wait_for_pages(f"{CRM}/staff.html", f"?v={version}"))


def step7():
    version = page_version("itdetiLearn", "index.html")
    state(learn_merge=merge_and_push("itdetiLearn"))
    html = wait_for_pages(f"{LEARN}/", f"?v={version}")
    check_assets(LEARN, html)
    script = wait_for_pages(f"{LEARN}/app.js?v={version}", "/auth/handoff/redeem")
    if "api('/auth/login'" not in script or "itdeti_learn_staff_" not in script.replace("KEY_PREFIX + '", "itdeti_learn_"):
        raise Stop("app.js ученического сайта на Pages без входа сотрудника")


def step8():
    prod_ops("counts", "--compare", str(COUNTS_FILE))
    # The old finance tables: nothing of them may have changed by the deploy (if the owner recorded a payment
    # meanwhile, this shows it as a difference: check by hand, nothing is rolled back for that).
    prod_ops("finance", "--compare", str(FINANCE_FILE))


STEPS = [
    (1, "preflight и counts", step1),
    (2, "бэкап", step2),
    (3, "миграция 016, postcheck, counts, контрольные суммы финансов", step3),
    (4, "сервер: слияние, push, ожидание деплоя", step4),
    (5, "смоук сервера", step5),
    (6, "CRM: слияние, push, GitHub Pages", step6),
    (7, "ученический сайт: слияние, push, GitHub Pages", step7),
    (8, "counts после выкладки", step8),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prod", action="store_true", help="обязательный флаг: скрипт меняет прод")
    parser.add_argument("--from-step", type=int, default=1)
    parser.add_argument("--only-step", type=int)
    parser.add_argument("--no-auto-revert", action="store_true", help="шаг 5: при провале смоука только сообщить, не откатывать")
    args = parser.parse_args()
    global NO_AUTO_REVERT
    NO_AUTO_REVERT = args.no_auto_revert
    if not args.prod:
        say("ОТКАЗ: это выкладка на прод; запускать с флагом --prod")
        return 2
    # Before anything touches production: all three repositories clean and on lms-mvp (otherwise the run
    # would stop half way, e.g. a new server with the old CRM).
    problems = []
    for repo in ("itdeti", "itdetiWeb", "itdetiLearn"):
        cwd = WORKSPACE / repo
        try:
            if sh(["git", "status", "--porcelain"], cwd):
                problems.append(f"{repo}: есть незакоммиченные изменения")
            if sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd) != "lms-mvp":
                problems.append(f"{repo}: текущая ветка не lms-mvp")
        except Stop as stop:
            problems.append(f"{repo}: {stop}")
    if problems:
        say("ОТКАЗ до начала выкладки:\n  " + "\n  ".join(problems))
        return 2
    for number, title, fn in STEPS:
        if (args.only_step and number != args.only_step) or (not args.only_step and number < args.from_step):
            continue
        say(f"\n=== Шаг {number}: {title} ===")
        try:
            fn()
        except Exception as error:  # Stop, and anything unexpected (network, broken state file): no traceback
            reason = str(error) if isinstance(error, Stop) else f"непредвиденная ошибка {type(error).__name__}: {str(error)[:300]}"
            say(f"\nСТОП на шаге {number}: {reason}")
            say(f"После устранения причины: python scripts/deploy_night6.py --prod --from-step {number}")
            return 1
    say("\nВыкладка завершена. Откат и аварийные действия: docs/RUNBOOK_EMERGENCY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
