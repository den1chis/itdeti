"""The ONLY tool that talks to the production database (night 4). Read-only, except `migrate`.

    python scripts/prod_ops.py --prod preflight
    python scripts/prod_ops.py --prod counts --save D:\\itdeti_backups\\counts_before.json
    python scripts/prod_ops.py --prod backup
    python scripts/prod_ops.py --prod migrate 014_security.sql
    python scripts/prod_ops.py --prod postcheck 014
    python scripts/prod_ops.py --prod counts --compare D:\\itdeti_backups\\counts_before.json

Target:
  --prod        PROD_DATABASE_URL from the secrets file (--secrets, default D:\\itdeti_secrets\\prod.env).
                Refused when the file or the key is missing, or when the URL points to the dev project or to
                localhost.
  --dev-check   the DEV database from itdeti/.env (for tests of this script). Refused when .env does not
                point to the dev project. --backup-dir is then required for backup/migrate.
Exactly one of the two must be given. The URL is never printed; the project is shown masked.

Commands:
  backup              every table of schema public -> JSON file itdeti_backup_<YYYY-MM-DD_HHMM>.json in the
                      backup directory (default D:\\itdeti_backups). Checked: rows in the file = count(*) of the
                      same snapshot, the file re-opens and parses. Prints table names and numbers only.
  preflight           checks from scripts/prod_preflight.sql
  postcheck <N>       checks from scripts/prod_postcheck_<N>.sql
                      Every check prints its result and "норма" / "НЕ НОРМА"; exit code 1 if any is not ok.
  counts              row counts of the main tables; --save FILE writes them, --compare FILE compares with a
                      saved file (exit code 1 on a difference that is not allowed growth).
  migrate <file>      a file from supabase/migrations/ with a number > 013. Only after a successful backup of
                      the SAME database made within the last 30 minutes; refused when the text contains anything
                      but additions (rule 3 of TZ_NIGHT4).

Exit codes: 0 ok, 1 a check is not ok, 2 refused / cannot run (a stop condition)."""
import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.apply_migration import MIGRATIONS_DIR, _strip_comments, static_problems  # noqa: E402
from scripts.devguard import DEV_PROJECT_REFS, LOCAL_HOSTS, is_dev_database  # noqa: E402

DEFAULT_SECRETS = Path(r"D:\itdeti_secrets\prod.env")
DEFAULT_BACKUP_DIR = Path(r"D:\itdeti_backups")
SECRET_KEY_NAME = "PROD_DATABASE_URL"
LAST_MANUAL_MIGRATION = 13
BACKUP_MAX_AGE_SECONDS = 30 * 60
BACKUP_PREFIX = "itdeti_backup_"

COUNT_TABLES = [
    "users", "students", "parents", "student_parents", "lessons", "events", "payments", "expenses",
    "student_schedule", "refresh_tokens", "lms_courses", "lms_modules", "lms_lessons", "lms_enrollments",
    "lms_lesson_progress", "tasks", "student_accounts", "notifications", "incoming_notifications",
    "audit_log", "auth_login_state", "auth_email_locks",
]
# The system keeps working while we deploy: these may grow between two `counts`.
GROWTH_ALLOWED = {"events", "notifications", "incoming_notifications", "audit_log", "auth_login_state",
                  "auth_email_locks"}
# refresh_tokens grows with every login and shrinks when the new server deletes expired rows.
ANY_CHANGE_ALLOWED = {"refresh_tokens"}


class Refused(Exception):
    """A stop condition: nothing was done."""


# ---------------------------------------------------------------- target
def to_dsn(url: str) -> str:
    url = url.strip().strip('"').strip("'")
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://"):]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.startswith("postgresql://"):
        raise Refused("the database URL must start with postgresql:// or postgresql+asyncpg://")
    # Percent-encode user and password: a raw "/", "?", "#" or "@" in the password breaks URL parsing, and
    # asyncpg then puts a piece of the password into its error text. unquote first, so an already encoded
    # password stays the same.
    _remember_secret(url)
    rest = url[len("postgresql://"):]
    userinfo, at, hostpart = rest.rpartition("@")
    if not at:
        raise Refused("the database URL has no user@host part (broken value in the secrets file?)")
    user, colon, password = userinfo.partition(":")
    for value in (password, unquote(password), quote(unquote(password), safe=""), user, unquote(user)):
        _remember_secret(value)
    if "." in unquote(user):
        _remember_secret(unquote(user).split(".", 1)[1])  # the project ref, shown masked only
    userinfo = quote(unquote(user), safe=".") + (":" + quote(unquote(password), safe="") if colon else "")
    url = f"postgresql://{userinfo}@{hostpart}"
    _remember_secret(url)
    try:
        parsed = urlparse(url)
        parsed.port  # noqa: B018 - raises ValueError on a broken port
        if not parsed.hostname:
            raise ValueError
    except ValueError:
        raise Refused("the database URL cannot be parsed (host/port); check the secrets file") from None
    return url


_SECRETS: list = []


def _remember_secret(value: str) -> None:
    if value and len(value) >= 4 and value not in _SECRETS:
        _SECRETS.append(value)


def redact(text: str) -> str:
    """Remove the database URL and the password (raw and encoded) from any text before printing it."""
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def project_ref(dsn: str) -> str:
    """Supabase project ref: user "postgres.<ref>" (pooler) or host "db.<ref>.supabase.co"; else the host."""
    parsed = urlparse(dsn)
    user, host = parsed.username or "", (parsed.hostname or "").lower()
    if "." in user:
        return user.split(".", 1)[1]
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return host.split(".")[1]
    return host


def mask(value: str) -> str:
    if len(value) <= 6:
        return value[:1] + "***"
    return f"{value[:4]}***{value[-2:]}"


def fingerprint(dsn: str) -> str:
    parsed = urlparse(dsn)
    raw = f"{project_ref(dsn)}|{(parsed.hostname or '').lower()}|{parsed.path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def read_secret(path: Path) -> str:
    if not path.exists():
        raise Refused(f"secrets file not found: {path} (it must hold a line {SECRET_KEY_NAME}=...)")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith(SECRET_KEY_NAME + "="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise Refused(f"{SECRET_KEY_NAME} is not set in {path}")


def resolve_target(prod: bool, dev_check: bool, secrets: Path):
    """-> (dsn, label). Raises Refused on any doubt about which database this is."""
    if prod == dev_check:
        raise Refused("give exactly one of --prod / --dev-check")
    if prod:
        dsn = to_dsn(read_secret(secrets))
        ref, host = project_ref(dsn), (urlparse(dsn).hostname or "").lower()
        if is_dev_database(dsn) or ref in DEV_PROJECT_REFS or host in LOCAL_HOSTS:
            raise Refused(f"{SECRET_KEY_NAME} points to the DEV project or to localhost; --prod is refused")
        return dsn, f"PROD  project {mask(ref)}  host {mask(host)}"
    from core.config import settings

    if not is_dev_database(settings.DATABASE_URL):
        raise Refused("--dev-check: itdeti/.env does not point to the dev project")
    dsn = to_dsn(settings.DATABASE_URL)
    return dsn, f"DEV   project {mask(project_ref(dsn))}"


async def connect(dsn: str):
    import asyncpg

    host = (urlparse(dsn).hostname or "").lower()
    # statement_cache_size=0: works through the Supabase pooler too.
    return await asyncpg.connect(dsn, ssl=None if host in LOCAL_HOSTS else "require", statement_cache_size=0,
                                 timeout=60, command_timeout=300)


# ---------------------------------------------------------------- checks (preflight / postcheck)
_BLOCK = re.compile(r"^--\s*\[(\d+)\]\s*(.*)$")
_EXPECT = re.compile(r"^--\s*@expect\s+(.*)$")


def parse_checks(text: str) -> list:
    """Blocks "-- [N] title" / "-- @expect RULE" / one SELECT ending with ';'."""
    checks, current = [], None
    for line in text.splitlines():
        head = _BLOCK.match(line.strip())
        if head:
            current = {"n": int(head.group(1)), "title": head.group(2).strip(), "expect": None, "sql": []}
            checks.append(current)
            continue
        if current is None:
            continue
        expect = _EXPECT.match(line.strip())
        if expect:
            current["expect"] = expect.group(1).strip()
        elif not line.strip().startswith("--"):
            current["sql"].append(line)
    for check in checks:
        check["sql"] = "\n".join(check["sql"]).strip().rstrip(";").strip()
        if not check["expect"]:
            raise Refused(f"check [{check['n']}] has no '-- @expect' line")
        code = _strip_comments(check["sql"])
        if not re.match(r"^\s*(SELECT|WITH)\b", code, re.I) or ";" in code:
            raise Refused(f"check [{check['n']}] must be exactly one SELECT")
        if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE)\b", code, re.I):
            raise Refused(f"check [{check['n']}] is not read-only")
    return checks


def _literal(text: str):
    text = text.strip()
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        return text.strip("'\"")


def evaluate(expect: str, rows: list) -> bool:
    """Rules: info | all_true | rows == N | rows >= N | scalar == V | scalar >= N."""
    if expect == "info":
        return True
    if expect == "all_true":
        return len(rows) == 1 and all(value is True for value in rows[0])
    match = re.fullmatch(r"(rows|scalar)\s*(==|>=)\s*(.+)", expect)
    if not match:
        raise Refused(f"unknown @expect rule: {expect}")
    kind, op, expected = match.group(1), match.group(2), _literal(match.group(3))
    if kind == "rows":
        actual = len(rows)
    else:
        if not rows:
            return False
        actual = rows[0][0]
    if op == "==":
        return actual == expected
    return actual is not None and actual >= expected


async def run_checks(conn, path: Path) -> bool:
    checks = parse_checks(path.read_text(encoding="utf-8"))
    all_ok = True
    for check in checks:
        async with conn.transaction(readonly=True):
            records = await conn.fetch(check["sql"])
        rows = [tuple(r.values()) for r in records]
        ok = evaluate(check["expect"], rows)
        all_ok = all_ok and ok
        verdict = "инфо" if check["expect"] == "info" else ("норма" if ok else "НЕ НОРМА")
        print(f"[{check['n']}] {check['title']}  (ожидается: {check['expect']})  ->  {verdict}")
        columns = list(records[0].keys()) if records else []
        for row in rows[:60]:
            print("      " + ", ".join(f"{c}={v}" for c, v in zip(columns, row)))
        if len(rows) > 60:
            print(f"      ... ещё {len(rows) - 60} строк")
        if not rows:
            print("      (нет строк)")
    print("ИТОГ: " + ("норма" if all_ok else "НЕ НОРМА"))
    return all_ok


# ---------------------------------------------------------------- counts
async def table_counts(conn) -> dict:
    async with conn.transaction(readonly=True):
        existing = {r["tablename"] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        counts = {}
        for table in COUNT_TABLES:
            if table in existing:
                counts[table] = await conn.fetchval(f'SELECT count(*) FROM public."{table}"')
    return counts


def compare_counts(before: dict, after: dict) -> list:
    """Differences that are NOT allowed (empty list = ok). Tables that appeared since `before` are fine."""
    problems = []
    for table, old in before.items():
        new = after.get(table)
        if new is None:
            problems.append(f"{table}: таблица исчезла")
        elif table in ANY_CHANGE_ALLOWED or new == old:
            continue
        elif table in GROWTH_ALLOWED and new > old:
            continue
        else:
            problems.append(f"{table}: было {old}, стало {new}")
    return problems


# ---------------------------------------------------------------- backup
def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


async def make_backup(conn, backup_dir: Path, dsn: str, label: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    tables = {}
    # One snapshot for everything: counts and data cannot drift apart.
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        names = [r["tablename"] for r in await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")]
        if not names:
            raise Refused("no tables in schema public: wrong database?")
        for name in names:
            count = await conn.fetchval(f'SELECT count(*) FROM public."{name}"')
            text = await conn.fetchval(f"SELECT coalesce(json_agg(t), '[]'::json)::text FROM public.\"{name}\" t")
            data = json.loads(text)
            if len(data) != count:
                raise Refused(f"backup check failed: {name} has {count} rows, the dump has {len(data)}")
            tables[name] = {"rows": count, "data": data}
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = backup_dir / f"{BACKUP_PREFIX}{stamp}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": label.split()[0],
        "fingerprint": fingerprint(dsn),
        "verified": True,
        "tables": tables,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=_json_default), encoding="utf-8")
    # Re-open and parse what is really on disk before calling it a backup.
    reread = json.loads(tmp.read_text(encoding="utf-8"))
    for name, info in tables.items():
        if len(reread["tables"][name]["data"]) != info["rows"]:
            tmp.unlink(missing_ok=True)
            raise Refused(f"backup file check failed for table {name}")
    tmp.replace(path)
    for name, info in tables.items():
        print(f"  {name}: {info['rows']}")
    total = sum(info["rows"] for info in tables.values())
    print(f"бэкап: {path}  таблиц {len(tables)}, строк {total}, размер {path.stat().st_size} байт, проверен")
    return path


def fresh_backup(backup_dir: Path, dsn: str, max_age: int = BACKUP_MAX_AGE_SECONDS):
    """The newest verified backup of THIS database not older than max_age seconds, or None."""
    if not backup_dir.exists():
        return None
    candidates = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if time.time() - path.stat().st_mtime > max_age:
            break
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("verified") is True and payload.get("fingerprint") == fingerprint(dsn) and payload.get("tables"):
            return path
    return None


# ---------------------------------------------------------------- migrate
_ADD_COLUMN = re.compile(r"\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?", re.I)
_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)", re.I)
_UPDATE = re.compile(r"\bUPDATE\s+([\w.\"]+)\s+SET\s+(.*?)(?:\bWHERE\b(.*?))?;", re.I | re.S)
_STRICT_FORBIDDEN = [
    (re.compile(r"\bDROP\b", re.I), "DROP"),
    (re.compile(r"(?<!\bON )\bDELETE\b", re.I), "DELETE"),  # "ON DELETE CASCADE" in a new foreign key is fine
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
    (re.compile(r"\bRENAME\b", re.I), "RENAME"),
    (re.compile(r"\bALTER\s+COLUMN\b", re.I), "ALTER COLUMN"),
    (re.compile(r"\bINSERT\b", re.I), "INSERT"),
    (re.compile(r"\b(GRANT|REVOKE)\b", re.I), "GRANT/REVOKE"),
    (re.compile(r"\bDISABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.I), "DISABLE ROW LEVEL SECURITY"),
]


def migration_problems(sql: str) -> list:
    """Rule 3 of TZ_NIGHT4: additions only. UPDATE is allowed only to fill columns added in the same file
    (with a WHERE that touches only rows where the new column is still empty)."""
    problems = list(static_problems(sql))
    code = _strip_comments(sql)
    # String literals may contain any words ('sha256:%'): do not scan inside them.
    bare = re.sub(r"[ \t]+", " ", re.sub(r"'(?:[^']|'')*'", "''", code))
    for pattern, name in _STRICT_FORBIDDEN:
        if pattern.search(bare) and f"forbidden statement: {name}" not in problems:
            problems.append(f"forbidden statement: {name}")
    new_columns = {c.lower() for c in _ADD_COLUMN.findall(bare)}
    for table, assignments, where in _UPDATE.findall(bare):
        targets = {t.lower() for t in re.findall(r"(?:^|,)\s*\"?(\w+)\"?\s*=", assignments)}
        if not targets or not targets <= new_columns:
            problems.append(f"UPDATE {table} changes existing columns: {', '.join(sorted(targets - new_columns)) or '?'}")
        elif not where or not any(re.search(rf"\b{re.escape(c)}\b", where, re.I) for c in targets):
            problems.append(f"UPDATE {table} has no WHERE on the new column (must fill only empty values)")
    if not re.search(r"\b(CREATE|ALTER)\b", bare, re.I):
        problems.append("nothing to apply (no CREATE / ALTER)")
    return problems


def resolve_migration(arg: str) -> Path:
    name = Path(arg).name
    path = (MIGRATIONS_DIR / name).resolve()
    if Path(arg).is_absolute() and Path(arg).resolve() != path:
        raise Refused(f"{arg} is not inside supabase/migrations/")
    match = re.fullmatch(r"(\d{3})_[\w-]+\.sql", name)
    if not match or path.parent != MIGRATIONS_DIR.resolve() or not path.exists():
        raise Refused(f"{name} is not a migration file NNN_name.sql in supabase/migrations/")
    if int(match.group(1)) <= LAST_MANUAL_MIGRATION:
        raise Refused(f"{name}: only migrations with a number > {LAST_MANUAL_MIGRATION:03d} may be applied by this tool")
    return path


async def migrate(conn, path: Path, backup_dir: Path, dsn: str, check_only: bool = False) -> None:
    sql = path.read_text(encoding="utf-8")
    problems = migration_problems(sql)
    if problems:
        raise Refused(f"{path.name}: " + "; ".join(problems))
    print(f"{path.name}: только добавления, RLS у новых таблиц — проверено")
    backup = fresh_backup(backup_dir, dsn)
    if backup is None:
        raise Refused(f"no verified backup of this database younger than {BACKUP_MAX_AGE_SECONDS // 60} minutes in "
                      f"{backup_dir}: run `backup` first")
    print(f"свежий бэкап: {backup.name}")
    if check_only:
        print("--check-only: миграция НЕ применялась")
        return
    # Simple-query protocol: the file carries its own BEGIN/COMMIT.
    await conn.execute(sql)
    print(f"применена {path.name}")


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="itdeti production database tool (read-only except migrate)")
    parser.add_argument("--prod", action="store_true")
    parser.add_argument("--dev-check", action="store_true")
    parser.add_argument("--secrets", type=Path, default=DEFAULT_SECRETS)
    parser.add_argument("--backup-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    sub.add_parser("preflight")
    post = sub.add_parser("postcheck")
    post.add_argument("number")
    counts = sub.add_parser("counts")
    counts.add_argument("--save", type=Path)
    counts.add_argument("--compare", type=Path)
    mig = sub.add_parser("migrate")
    mig.add_argument("file")
    mig.add_argument("--check-only", action="store_true")
    return parser


async def run(args) -> int:
    dsn, label = resolve_target(args.prod, args.dev_check, args.secrets)
    print(f"база: {label}")
    backup_dir = args.backup_dir
    if args.command in ("backup", "migrate") and backup_dir is None:
        if not args.prod:
            raise Refused("--dev-check: give --backup-dir (dev dumps never go to the production backup folder)")
        backup_dir = DEFAULT_BACKUP_DIR
    if args.command == "postcheck":
        if not re.fullmatch(r"\d{1,3}", args.number):
            raise Refused("postcheck needs a migration number, e.g. 014")
        checks_path = ROOT / "scripts" / f"prod_postcheck_{int(args.number):03d}.sql"
        if not checks_path.exists():
            raise Refused(f"{checks_path.name} not found")
    elif args.command == "preflight":
        checks_path = ROOT / "scripts" / "prod_preflight.sql"
    migration = resolve_migration(args.file) if args.command == "migrate" else None

    conn = await connect(dsn)
    try:
        if args.command in ("preflight", "postcheck"):
            return 0 if await run_checks(conn, checks_path) else 1
        if args.command == "backup":
            await make_backup(conn, backup_dir, dsn, label)
            return 0
        if args.command == "counts":
            counts = await table_counts(conn)
            for table, number in counts.items():
                print(f"  {table}: {number}")
            if args.save:
                args.save.parent.mkdir(parents=True, exist_ok=True)
                args.save.write_text(json.dumps(counts, indent=1), encoding="utf-8")
                print(f"сохранено: {args.save}")
            if args.compare:
                problems = compare_counts(json.loads(args.compare.read_text(encoding="utf-8")), counts)
                for problem in problems:
                    print("  РАСХОЖДЕНИЕ " + problem)
                print("сравнение с " + args.compare.name + ": " + ("норма" if not problems else "НЕ НОРМА"))
                return 0 if not problems else 1
            return 0
        if args.command == "migrate":
            await migrate(conn, migration, backup_dir, dsn, check_only=args.check_only)
            return 0
        raise Refused(f"unknown command {args.command}")
    finally:
        await conn.close()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except Refused as refusal:
        print(f"ОТКАЗ: {redact(str(refusal))}")
        return 2
    except Exception as error:
        # No traceback: it could carry the URL. Only the type and a redacted message; a ValueError (URL
        # parsing inside the driver) may quote a piece of the URL, so its text is never shown.
        text = "" if isinstance(error, ValueError) else redact(str(error))[:500]
        print(f"ОШИБКА: {type(error).__name__}: {text}")
        print("Действие остановлено; если это ошибка подключения — проверьте сеть и PROD_DATABASE_URL в файле секретов.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
