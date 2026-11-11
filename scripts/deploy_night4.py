"""Deploy of night 4 to production, section 4 of TZ_NIGHT4 (the manual runbook of 2026-11-11, automated).

    python scripts/deploy_night4.py --prod                 # all steps, in order
    python scripts/deploy_night4.py --prod --from-step 4   # continue after a stop
    python scripts/deploy_night4.py --prod --only-step 5   # one step (e.g. repeat the server smoke test)

Steps:
  1  prod_ops preflight + counts (saved to the backup folder)
  2  prod_ops backup
  3  prod_ops migrate 014_security.sql, postcheck 014, counts compared with step 1
  4  itdeti: push lms-mvp, merge --no-ff into main, push main, wait for the new /health version on Render
  5  server smoke test; on failure the server merge is reverted (the migration stays: it only adds)
  6  itdetiWeb: push, merge, push main, wait for GitHub Pages (new ?v=), every referenced file answers 200
  7  itdetiLearn: push, merge, push main, the site answers 200 with the new index.html
  8  prod_ops counts compared with step 1 again

Every step stops the whole run on the first problem (exit code 1) and prints what to do. Nothing here prints
or stores the database URL: the database is touched only through scripts/prod_ops.py.
Logins on production use only addresses that cannot exist (nobody-check-...@example.com); real accounts are
never tried."""
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
MIGRATION = "014_security.sql"
BACKUP_DIR = Path(r"D:\itdeti_backups")
COUNTS_FILE = BACKUP_DIR / "counts_night4_before.json"
STATE_FILE = BACKUP_DIR / "deploy_night4_state.json"
MERGE_MESSAGE = {
    "itdeti": "Merge lms-mvp: night 4 (CRM login throttling, audit log, hashed refresh tokens, CF-Connecting-IP)",
    "itdetiWeb": "Merge lms-mvp: night 4 (audit log page, login lock message, favicon, new ?v=)",
    "itdetiLearn": "Merge lms-mvp: night 4 (favicon, server waking-up hint)",
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
    return failures


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


def step2():
    prod_ops("backup")


def step3():
    prod_ops("migrate", MIGRATION)
    prod_ops("postcheck", "014")
    prod_ops("counts", "--compare", str(COUNTS_FILE))


def step4():
    reverted = state().get("server_reverted")
    if reverted:
        raise Stop(f"сервер уже откатывался в этой выкладке (коммит отката {reverted[:7]} в main). Повторное слияние "
                   "не вернёт код ночи 4: сначала вручную `git revert <коммит отката>` в main (docs/RUNBOOK_EMERGENCY.md, "
                   "раздел 2), затем удалить ключ server_reverted из D:\\itdeti_backups\\deploy_night4_state.json")
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
    check_assets(CRM, html)
    check_assets(CRM, wait_for_pages(f"{CRM}/index.html", f"?v={version}"))


def step7():
    version = page_version("itdetiLearn", "index.html")
    state(learn_merge=merge_and_push("itdetiLearn"))
    html = wait_for_pages(f"{LEARN}/", f"?v={version}")
    check_assets(LEARN, html)


def step8():
    prod_ops("counts", "--compare", str(COUNTS_FILE))


STEPS = [
    (1, "preflight и counts", step1),
    (2, "бэкап", step2),
    (3, "миграция 014, postcheck, counts", step3),
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
            say(f"После устранения причины: python scripts/deploy_night4.py --prod --from-step {number}")
            return 1
    say("\nВыкладка завершена. Откат и аварийные действия: docs/RUNBOOK_EMERGENCY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
