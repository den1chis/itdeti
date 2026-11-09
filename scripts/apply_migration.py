"""Apply one SQL migration file to the DEV database (refuses to run against anything else).

    python scripts/apply_migration.py migrations/001_student_accounts.sql
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402

from core.config import settings  # noqa: E402
from scripts.devguard import require_dev_database  # noqa: E402


async def main(path: Path) -> None:
    require_dev_database(settings.DATABASE_URL, settings.APP_ENV)
    sql = path.read_text(encoding="utf-8")
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn, ssl="require" if "localhost" not in dsn else None)
    try:
        # Simple-query protocol: the file may contain BEGIN/COMMIT and statements outside a transaction.
        await conn.execute(sql)
    finally:
        await conn.close()
    print(f"applied {path.name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(Path(sys.argv[1])))
