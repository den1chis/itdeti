"""Inventory of every route of the app and its access protection -> markdown table (docs/ROUTES_ACCESS.md).

    python scripts/routes_inventory.py                       # print the table
    python scripts/routes_inventory.py --json out.json       # also dump the rows as JSON
    python scripts/routes_inventory.py --before before.json  # add a "before" column from an earlier dump

Access is read from the dependency tree of each route: access-class markers (core.access) when present,
otherwise the legacy dependencies (get_current_user, require_role(...), get_current_student)."""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
# The documentation routes exist only with ENABLE_DOCS=true (night 3); list them anyway.
os.environ["ENABLE_DOCS"] = "true"
DOCS_LABEL = "public, only with ENABLE_DOCS=true (404 otherwise)"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.routing import APIRoute  # noqa: E402


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _walk(dep)


def _legacy_label(call) -> str:
    name = getattr(call, "__name__", "")
    if name == "checker" and call.__closure__:
        for cell in call.__closure__:
            value = cell.cell_contents
            if isinstance(value, tuple):
                return "role in " + "/".join(value)
    return {"get_current_user": "any CRM user", "get_current_student": "student token"}.get(name, "")


def route_rows(app):
    try:
        from core.access import PUBLIC_ROUTES, access_label
    except Exception:  # before night 2: no core.access yet
        PUBLIC_ROUTES, access_label = set(), None
    rows = []
    for route in app.routes:
        methods = sorted(getattr(route, "methods", None) or [])
        for method in methods:
            if method == "HEAD":
                continue
            if isinstance(route, APIRoute):
                router = route.endpoint.__module__.replace("routers.", "")
                calls = list(_walk(route.dependant))
                label = access_label(route) if access_label else ""
                if not label:
                    legacy = [l for l in (_legacy_label(c) for c in calls) if l]
                    # strictest legacy check wins (role in ... is stricter than "any CRM user")
                    label = next((l for l in legacy if l.startswith("role in")), legacy[0] if legacy else "")
                if not label and (method, route.path) in PUBLIC_ROUTES:
                    label = "public"
                rows.append({"method": method, "path": route.path, "router": router, "access": label or "NONE"})
            else:
                rows.append({"method": method, "path": route.path, "router": "fastapi (docs)",
                             "access": DOCS_LABEL if (method, route.path) in PUBLIC_ROUTES else "public (docs)"})
    rows.sort(key=lambda r: (r["router"], r["path"], r["method"]))
    return rows


def to_markdown(rows, before=None):
    before_map = {(r["method"], r["path"]): r["access"] for r in (before or [])}
    head = "| Router | Method | Path | " + ("Before night 2 | " if before else "") + "Access |"
    sep = "|---|---|---|" + ("---|" if before else "") + "---|"
    lines = [head, sep]
    for r in rows:
        cells = [r["router"], r["method"], f"`{r['path']}`"]
        if before:
            cells.append(before_map.get((r["method"], r["path"]), "— (new)"))
        cells.append(r["access"])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    parser.add_argument("--before")
    args = parser.parse_args()
    import main as app_main

    rows = route_rows(app_main.app)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    before = json.loads(Path(args.before).read_text(encoding="utf-8")) if args.before else None
    print(to_markdown(rows, before))


if __name__ == "__main__":
    main()
