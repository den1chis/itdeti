"""Apply SQL migration(s) from supabase/migrations/ to the DEV database (refuses to run against anything else).

    python scripts/apply_migration.py 013                                      # by number
    python scripts/apply_migration.py supabase/migrations/013_access_model.sql # by path
    python scripts/apply_migration.py 012 013                                  # several, in the given order
    python scripts/apply_migration.py --check 013                              # only run the static checks

Safety:
- only the dev project (scripts/devguard.py) or localhost; APP_ENV=production is refused;
- only files inside supabase/migrations/;
- static check "additive only": DROP / RENAME / ALTER COLUMN ... TYPE / TRUNCATE / DELETE FROM are refused,
  and every CREATE TABLE must be followed by ENABLE ROW LEVEL SECURITY for that table;
- idempotency is the migration's job (IF NOT EXISTS everywhere): applying a file twice must be harmless.
  Every migration from 012 on is written that way.
"""
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIGRATIONS_DIR = ROOT / "supabase" / "migrations"

FORBIDDEN = [
    (re.compile(r"\bDROP\s+(TABLE|COLUMN|TYPE|INDEX|SCHEMA|CONSTRAINT|FUNCTION|TRIGGER|VIEW|POLICY)\b", re.I), "DROP"),
    (re.compile(r"\bRENAME\b", re.I), "RENAME"),
    (re.compile(r"\bALTER\s+COLUMN\s+\w+\s+(SET\s+DATA\s+)?TYPE\b", re.I), "ALTER COLUMN ... TYPE"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b", re.I), "DELETE FROM"),
]
CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)", re.I)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def static_problems(sql: str) -> list:
    code = _strip_comments(sql)
    problems = [f"forbidden statement: {name}" for pattern, name in FORBIDDEN if pattern.search(code)]
    for table in CREATE_TABLE.findall(code):
        name = table.strip('"')
        rls = re.compile(rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?\"?{re.escape(name)}\"?\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I)
        if not rls.search(code):
            problems.append(f"table {name} is created without ENABLE ROW LEVEL SECURITY")
    return problems


def resolve(arg: str) -> Path:
    if re.fullmatch(r"\d{1,3}", arg):
        found = sorted(MIGRATIONS_DIR.glob(f"{int(arg):03d}_*.sql"))
        if len(found) != 1:
            sys.exit(f"REFUSED: expected exactly one migration {int(arg):03d}_*.sql in {MIGRATIONS_DIR}, found {len(found)}")
        return found[0]
    path = Path(arg).resolve()
    if path.parent != MIGRATIONS_DIR.resolve() or path.suffix != ".sql" or not path.exists():
        sys.exit(f"REFUSED: {arg} is not a .sql file in supabase/migrations/")
    return path


async def apply(paths) -> None:
    import asyncpg

    from core.config import settings
    from scripts.devguard import require_dev_database

    require_dev_database(settings.DATABASE_URL, settings.APP_ENV)
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn, ssl="require" if "localhost" not in dsn and "127.0.0.1" not in dsn else None)
    try:
        for path in paths:
            # Simple-query protocol: the file may contain BEGIN/COMMIT and statements outside a transaction.
            await conn.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")
    finally:
        await conn.close()


def main(argv) -> None:
    check_only = "--check" in argv
    args = [a for a in argv if a != "--check"]
    if not args:
        sys.exit(__doc__)
    paths = [resolve(a) for a in args]
    for path in paths:
        problems = static_problems(path.read_text(encoding="utf-8"))
        if problems:
            sys.exit(f"REFUSED {path.name}: " + "; ".join(problems))
        print(f"checked {path.name}: additive only, RLS on new tables")
    if not check_only:
        asyncio.run(apply(paths))


if __name__ == "__main__":
    main(sys.argv[1:])
