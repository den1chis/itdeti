"""Night 6: Финансы by period, next-month forecast, savings (migration 016).

Integration tests against the DEV database (.env), like the other night tests: every run creates its own uniquely
named rows and deletes them at the end. The dev database holds other rows too, so sums are checked as DELTAS
(before / after this run's rows) or in periods far in the future. Refuses to run against a non-dev database."""
import subprocess
import sys
import uuid
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from core.config import settings
from scripts.devguard import is_dev_database

if not is_dev_database(settings.DATABASE_URL):  # pragma: no cover
    pytest.skip("integration tests run only against the dev database", allow_module_level=True)

import main  # noqa: E402
from core import access  # noqa: E402
from core.database import AsyncSessionLocal  # noqa: E402
from core.security import create_access_token, hash_password  # noqa: E402
from core.student_auth import create_student_token  # noqa: E402
from models.expense import Expense  # noqa: E402
from models.payment import Payment  # noqa: E402
from models.recurring_income import RecurringIncome, RecurringIncomeRecord  # noqa: E402
from models.savings import SavingsAccount, SavingsMovement  # noqa: E402
from models.schedule import StudentScheduleSlot  # noqa: E402
from models.security import AuditLog  # noqa: E402
from models.student import Student  # noqa: E402
from models.user import User  # noqa: E402
from routers.finance import LOCAL_TZ  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUN = uuid.uuid4().hex[:6]
USERS = {"admin": ["admin"], "T": ["teacher"], "M": ["methodist"], "TM": ["teacher", "methodist"]}
NEW_ROUTES = {
    ("GET", "/finance/period"), ("GET", "/finance/period/operations"), ("GET", "/finance/forecast"),
    ("GET", "/finance/savings"), ("POST", "/finance/savings/accounts"), ("PATCH", "/finance/savings/accounts/{account_id}"),
    ("DELETE", "/finance/savings/accounts/{account_id}"), ("GET", "/finance/savings/movements"),
    ("POST", "/finance/savings/movements"), ("PATCH", "/finance/savings/movements/{movement_id}"),
    ("DELETE", "/finance/savings/movements/{movement_id}"), ("GET", "/finance/savings/monthly"),
}
# A quiet period far in the future: nobody records money there, so absolute numbers can be checked.
FUTURE = date(2041, 3, 1)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def run(client, fn, *args):
    return client.portal.call(fn, *args)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def today() -> date:
    return datetime.now(LOCAL_TZ).date()


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


async def _create_world():
    w = {}
    async with AsyncSessionLocal() as db:
        hashed = hash_password("pw6-" + RUN)
        for key, roles in USERS.items():
            w[key] = User(email=f"t6-{key.lower()}-{RUN}@example.com", full_name=f"t6 {key} {RUN}", role=roles[0],
                          roles=roles, is_active=True, password_hash=hashed)
            db.add(w[key])
        w["S"] = Student(full_name=f"Т6S {RUN}", lesson_price=Decimal("1000"), balance=Decimal("0"), is_active=True)
        db.add(w["S"])
        await db.commit()
    return w


async def _cleanup(w):
    async with AsyncSessionLocal() as db:
        user_ids = [w[k].id for k in USERS]
        accounts = (await db.execute(select(SavingsAccount.id).where(SavingsAccount.name.like(f"%{RUN}%")))).scalars().all()
        await db.execute(delete(SavingsMovement).where(SavingsMovement.account_id.in_(accounts)))
        await db.execute(delete(SavingsAccount).where(SavingsAccount.id.in_(accounts)))
        await db.execute(delete(Payment).where(Payment.student_id == w["S"].id))
        await db.execute(delete(StudentScheduleSlot).where(StudentScheduleSlot.student_id == w["S"].id))
        await db.execute(delete(Student).where(Student.id == w["S"].id))
        incomes = (await db.execute(select(RecurringIncome.id).where(RecurringIncome.name.like(f"%{RUN}%")))).scalars().all()
        await db.execute(delete(RecurringIncomeRecord).where(RecurringIncomeRecord.recurring_income_id.in_(incomes)))
        await db.execute(delete(RecurringIncome).where(RecurringIncome.id.in_(incomes)))
        await db.execute(delete(Expense).where(Expense.description.like(f"%{RUN}%")))
        await db.execute(delete(AuditLog).where(or_(AuditLog.actor_user_id.in_(user_ids),
                                                    AuditLog.target_id.in_([str(a) for a in accounts]))))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


@pytest.fixture(scope="module")
def world(client):
    w = run(client, _create_world)
    yield w
    run(client, _cleanup, w)


def tok(w, key):
    return bearer(create_access_token(str(w[key].id), USERS[key][0]))


async def _add(*rows):
    async with AsyncSessionLocal() as db:
        db.add_all(rows)
        await db.commit()


def period_q(start: date, end: date) -> str:
    return f"from={start.isoformat()}&to={end.isoformat()}"


# ================================================================ access
def test_new_routes_exist_and_are_admin_only():
    found = set()
    for route in main.app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD"}:
                if (method, route.path) in NEW_ROUTES:
                    found.add((method, route.path))
                    assert access.access_label(route) == "admin", route.path
    assert found == NEW_ROUTES


def test_teacher_methodist_student_and_anonymous_get_refused(client, world):
    student = bearer(create_student_token(uuid.uuid4()))
    some_id = str(uuid.uuid4())
    for method, path in sorted(NEW_ROUTES):
        url = path.replace("{account_id}", some_id).replace("{movement_id}", some_id)
        body = {"name": "x", "kind": "cash", "account_id": some_id, "amount": 1, "movement_date": "2041-03-01"}
        for key in ("T", "M", "TM"):
            r = client.request(method, url, headers=tok(world, key), json=body)
            assert r.status_code == 403, f"{key} {method} {url} -> {r.status_code}"
        assert client.request(method, url, json=body).status_code in (401, 403), f"anon {method} {url}"
        assert client.request(method, url, headers=student, json=body).status_code in (401, 403), f"student {method} {url}"
    # nothing was created by the refused calls
    assert run(client, _count_accounts) == 0


async def _count_accounts():
    async with AsyncSessionLocal() as db:
        return len((await db.execute(select(SavingsAccount.id).where(SavingsAccount.name == "x"))).scalars().all())


def test_routes_access_doc_lists_new_routes():
    doc = (ROOT / "docs" / "ROUTES_ACCESS.md").read_text(encoding="utf-8")
    for _, path in NEW_ROUTES:
        assert f"`{path}`" in doc, path


# ================================================================ period summary
def test_period_summary_defaults_to_current_month_and_equals_old_summary(client, world):
    admin = tok(world, "admin")
    old = client.get("/finance/summary", headers=admin).json()
    new = client.get("/finance/period", headers=admin).json()
    assert new["date_from"] == old["month_start"] and new["date_to"] == old["month_end"]
    for new_key, old_key in (("total_income", "total_income"), ("income_from_students", "income_from_students"),
                             ("recurring_income_total", "recurring_income_total"), ("expenses_total", "expenses_total"),
                             ("net", "net_profit")):
        assert Decimal(new[new_key]) == Decimal(old[old_key]), new_key
    assert {k: Decimal(v) for k, v in new["expenses_by_category"].items()} == \
        {k: Decimal(v) for k, v in old["expenses_by_category"].items()}


def test_period_summary_counts_payments_recurring_and_expenses_of_the_period(client, world):
    admin = tok(world, "admin")
    start, end = FUTURE, date(2041, 3, 31)
    before = client.get(f"/finance/period?{period_q(start, end)}", headers=admin).json()
    income = RecurringIncome(id=uuid.uuid4(), name=f"t6 ri {RUN}", amount=Decimal("7000"), payment_method="kaspi", day_of_month=5, is_active=False)
    run(client, _add, income)
    run(client, _add,
        Payment(student_id=world["S"].id, amount=Decimal("5000"), payment_method="cash",
                recorded_at=datetime.combine(date(2041, 3, 1), time(0, 30), tzinfo=LOCAL_TZ)),   # first minute of the day
        Payment(student_id=world["S"].id, amount=Decimal("3000"), payment_method="cash",
                recorded_at=datetime.combine(date(2041, 3, 31), time(23, 50), tzinfo=LOCAL_TZ)),
        Payment(student_id=world["S"].id, amount=Decimal("900"), payment_method="cash",
                recorded_at=datetime.combine(date(2041, 4, 1), time(0, 5), tzinfo=LOCAL_TZ)),    # next period
        Payment(student_id=world["S"].id, amount=Decimal("100"), payment_method="cash", is_cancelled=True,
                recorded_at=datetime.combine(date(2041, 3, 10), time(12), tzinfo=LOCAL_TZ)),     # cancelled
        RecurringIncomeRecord(recurring_income_id=income.id, recurring_period=date(2041, 3, 1), amount=Decimal("7000"),
                              payment_method="kaspi", received_date=date(2041, 3, 5)),
        Expense(category="rent", amount=Decimal("4000"), payment_method="cash", expense_date=date(2041, 3, 15),
                description=f"t6 {RUN}"),
        Expense(category="tax", amount=Decimal("700"), payment_method="cash", expense_date=date(2041, 3, 16),
                description=f"t6 {RUN}", is_cancelled=True))
    after = client.get(f"/finance/period?{period_q(start, end)}", headers=admin).json()

    def delta(key):
        return Decimal(after[key]) - Decimal(before[key])

    assert delta("income_from_students") == Decimal("8000")
    assert delta("recurring_income_total") == Decimal("7000")
    assert delta("total_income") == Decimal("15000")
    assert delta("expenses_total") == Decimal("4000")
    assert delta("net") == Decimal("11000")
    assert Decimal(after["expenses_by_category"]["rent"]) - Decimal(before["expenses_by_category"].get("rent", 0)) == Decimal("4000")

    ops = client.get(f"/finance/period/operations?{period_q(start, end)}", headers=admin).json()
    mine = [o for o in ops if o.get("student_id") == str(world["S"].id)]
    assert sorted(Decimal(o["amount"]) for o in mine) == [Decimal("3000"), Decimal("5000")]
    assert any(o["source_type"] == "recurring_income" and Decimal(o["amount"]) == Decimal("7000") for o in ops)
    assert any(o["source_type"] == "expense" and o["description"] == f"t6 {RUN}" for o in ops)
    assert all(start.isoformat() <= o["date"][:10] <= end.isoformat() for o in ops)
    with_cancelled = client.get(f"/finance/period/operations?{period_q(start, end)}&include_cancelled=true", headers=admin).json()
    assert len(with_cancelled) == len(ops) + 2

    # a one-day period inside
    day = client.get(f"/finance/period?{period_q(date(2041, 3, 31), date(2041, 3, 31))}", headers=admin).json()
    assert Decimal(day["income_from_students"]) >= Decimal("3000")


def test_period_validation(client, world):
    admin = tok(world, "admin")
    assert client.get("/finance/period?from=2041-03-10&to=2041-03-01", headers=admin).status_code == 400
    assert client.get("/finance/period?from=2041-03-10", headers=admin).status_code == 400
    assert client.get("/finance/period?from=2000-01-01&to=2041-03-01", headers=admin).status_code == 400
    assert client.get("/finance/period?from=bad&to=2041-03-01", headers=admin).status_code == 422


def test_old_finance_answers_keep_their_shape(client, world):
    admin = tok(world, "admin")
    summary = client.get("/finance/summary", headers=admin).json()
    assert set(summary) == {
        "month_start", "month_end", "income_from_students", "recurring_income_total", "total_income", "expenses_total",
        "expenses_by_category", "net_profit", "total_school_balance", "student_balances_total",
        "negative_student_balances_total", "scheduled_lessons_this_month", "scheduled_income_this_month",
        "forecast_income_remaining", "active_students", "monthly_forecast_income", "planned_recurring_income",
        "planned_recurring_expenses", "recurring_income_remaining", "recurring_expenses_remaining"}
    for url in ("/finance/transactions?limit=5", "/finance/payments", "/finance/expenses", "/finance/recurring-expenses",
                "/finance/recurring-incomes", "/finance/recurring-income-records"):
        assert client.get(url, headers=admin).status_code == 200, url


# ================================================================ forecast
def test_forecast_both_modes(client, world):
    admin = tok(world, "admin")
    before = client.get("/finance/forecast", headers=admin).json()
    next_start = add_months(today(), 1)
    next_end = date(next_start.year, next_start.month, monthrange(next_start.year, next_start.month)[1])
    assert before["month_start"] == next_start.isoformat() and before["month_end"] == next_end.isoformat()
    assert before["default_mode"] == "schedule"

    # schedule: one weekly slot (Monday) of an active student at 1000 + an active recurring income 7000
    run(client, _add,
        StudentScheduleSlot(student_id=world["S"].id, day_of_week=0, start_time=time(15), duration_minutes=60,
                            valid_from=today(), is_active=True),
        RecurringIncome(name=f"t6 forecast {RUN}", amount=Decimal("7000"), payment_method="kaspi", day_of_month=1, is_active=True))
    # average: payments in each of the three previous FULL months (3000, 6000, 9000 -> mean 6000)
    rows = []
    for back, amount in ((3, "3000"), (2, "6000"), (1, "9000")):
        month = add_months(today(), -back)
        rows.append(Payment(student_id=world["S"].id, amount=Decimal(amount), payment_method="cash",
                            recorded_at=datetime.combine(month + timedelta(days=9), time(12), tzinfo=LOCAL_TZ)))
    rows.append(Payment(student_id=world["S"].id, amount=Decimal("50000"), payment_method="cash",   # this month: not counted
                        recorded_at=datetime.combine(today(), time(0, 1), tzinfo=LOCAL_TZ)))
    run(client, _add, *rows)
    after = client.get("/finance/forecast", headers=admin).json()

    mondays = sum(1 for d in range((next_end - next_start).days + 1) if (next_start + timedelta(days=d)).weekday() == 0)
    s0, s1 = before["schedule"], after["schedule"]
    assert s1["lessons_count"] - s0["lessons_count"] == mondays
    assert Decimal(s1["students_income"]) - Decimal(s0["students_income"]) == Decimal(1000 * mondays)
    assert Decimal(s1["recurring_income"]) - Decimal(s0["recurring_income"]) == Decimal("7000")
    assert Decimal(s1["total"]) == Decimal(s1["students_income"]) + Decimal(s1["recurring_income"])
    assert any(i["name"] == f"t6 forecast {RUN}" for i in s1["recurring_items"])

    a0, a1 = before["average"], after["average"]
    assert [m["month"] for m in a1["months"]] == [f"{add_months(today(), -b):%Y-%m}" for b in (3, 2, 1)]
    assert Decimal(a1["students_income"]) - Decimal(a0["students_income"]) == Decimal("6000")
    assert Decimal(a1["total"]) == Decimal(a1["students_income"]) + Decimal(a1["recurring_income"])


# ================================================================ savings
def test_savings_accounts_movements_balances_and_rules(client, world):
    admin = tok(world, "admin")
    total_before = client.get("/finance/savings", headers=admin).json()["total"]

    r = client.post("/finance/savings/accounts", headers=admin,
                    json={"name": f"Депозит Halyk {RUN}", "kind": "deposit", "opening_balance": 100000, "opening_date": "2041-01-10"})
    assert r.status_code == 201, r.text
    dep = r.json()
    assert dep["balance"] == 100000 and dep["movements_count"] == 1
    r = client.post("/finance/savings/accounts", headers=admin, json={"name": f"Наличные {RUN}", "kind": "cash"})
    assert r.status_code == 201 and r.json()["balance"] == 0
    cash = r.json()

    # validation: whole tenge, positive, known kinds
    bad = [{"account_id": dep["id"], "kind": "deposit", "amount": 10.5, "movement_date": "2041-02-01"},
           {"account_id": dep["id"], "kind": "deposit", "amount": 0, "movement_date": "2041-02-01"},
           {"account_id": dep["id"], "kind": "gift", "amount": 10, "movement_date": "2041-02-01"},
           {"account_id": dep["id"], "kind": "deposit", "amount": 10, "movement_date": "1990-02-01"}]
    for body in bad:
        assert client.post("/finance/savings/movements", headers=admin, json=body).status_code == 422, body
    assert client.post("/finance/savings/accounts", headers=admin, json={"name": "  ", "kind": "cash"}).status_code == 422
    assert client.post("/finance/savings/accounts", headers=admin, json={"name": "y", "kind": "gold"}).status_code == 422

    def move(account, kind, amount, day, comment=None):
        return client.post("/finance/savings/movements", headers=admin,
                           json={"account_id": account["id"], "kind": kind, "amount": amount, "movement_date": day, "comment": comment})

    assert move(dep, "deposit", 5000, "2041-02-03", "<b>проценты</b>").status_code == 201
    assert move(dep, "withdrawal", 200000, "2041-02-05").status_code == 409          # more than the balance
    w = move(dep, "withdrawal", 30000, "2041-03-02")
    assert w.status_code == 201
    assert move(cash, "deposit", 20000, "2041-03-03").status_code == 201
    assert move(cash, "withdrawal", 20001, "2041-03-04").status_code == 409

    overview = client.get("/finance/savings", headers=admin).json()
    balances = {a["id"]: a["balance"] for a in overview["accounts"]}
    assert balances[dep["id"]] == 75000 and balances[cash["id"]] == 20000
    assert overview["total"] - total_before == 95000

    movements = client.get(f"/finance/savings/movements?{period_q(date(2041, 2, 1), date(2041, 2, 28))}", headers=admin).json()
    mine = [m for m in movements if m["account_id"] == dep["id"]]
    assert [(m["kind"], m["amount"]) for m in mine] == [("deposit", 5000)]
    assert mine[0]["comment"] == "<b>проценты</b>" and mine[0]["account_name"] == f"Депозит Halyk {RUN}"
    only_cash = client.get(f"/finance/savings/movements?{period_q(date(2041, 1, 1), date(2041, 12, 31))}&account_id={cash['id']}",
                           headers=admin).json()
    assert [m["amount"] for m in only_cash] == [20000]

    # edit: withdrawal 30000 -> 80000 still fits (105000 - 80000); 110000 does not
    wid = w.json()["id"]
    assert client.patch(f"/finance/savings/movements/{wid}", headers=admin,
                        json={"kind": "withdrawal", "amount": 110000, "movement_date": "2041-03-02"}).status_code == 409
    r = client.patch(f"/finance/savings/movements/{wid}", headers=admin,
                     json={"kind": "withdrawal", "amount": 80000, "movement_date": "2041-03-02", "comment": "ремонт"})
    assert r.status_code == 200 and r.json()["amount"] == 80000
    # cancelling the opening deposit would make the balance negative -> 409; cancelling the withdrawal is fine
    opening = client.get(f"/finance/savings/movements?{period_q(date(2041, 1, 1), date(2041, 1, 31))}&account_id={dep['id']}",
                         headers=admin).json()[0]
    assert opening["comment"] == "Начальный остаток" and opening["amount"] == 100000
    assert client.delete(f"/finance/savings/movements/{opening['id']}", headers=admin).status_code == 409
    r = client.delete(f"/finance/savings/movements/{wid}", headers=admin)
    assert r.status_code == 200 and r.json()["is_cancelled"] is True
    assert client.delete(f"/finance/savings/movements/{wid}", headers=admin).status_code == 200   # repeat: no change
    assert client.patch(f"/finance/savings/movements/{wid}", headers=admin,
                        json={"kind": "withdrawal", "amount": 1, "movement_date": "2041-03-02"}).status_code == 409
    balances = {a["id"]: a["balance"] for a in client.get("/finance/savings", headers=admin).json()["accounts"]}
    assert balances[dep["id"]] == 105000

    # rename, archive (no new movements), unarchive; delete only without movements
    r = client.patch(f"/finance/savings/accounts/{dep['id']}", headers=admin, json={"name": f"Депозит Kaspi {RUN}"})
    assert r.status_code == 200 and r.json()["name"] == f"Депозит Kaspi {RUN}"
    r = client.patch(f"/finance/savings/accounts/{dep['id']}", headers=admin, json={"is_archived": True})
    assert r.status_code == 200 and r.json()["is_archived"] is True and r.json()["balance"] == 105000
    assert move(dep, "deposit", 1, "2041-03-05").status_code == 409
    assert client.delete(f"/finance/savings/accounts/{dep['id']}", headers=admin).status_code == 409
    assert client.patch(f"/finance/savings/accounts/{dep['id']}", headers=admin, json={"is_archived": False}).json()["is_archived"] is False
    empty = client.post("/finance/savings/accounts", headers=admin, json={"name": f"Пустой {RUN}", "kind": "cash"}).json()
    assert client.delete(f"/finance/savings/accounts/{empty['id']}", headers=admin).status_code == 204
    assert client.delete(f"/finance/savings/accounts/{empty['id']}", headers=admin).status_code == 404
    assert client.delete(f"/finance/savings/accounts/{cash['id']}", headers=admin).status_code == 409
    assert move({"id": str(uuid.uuid4())}, "deposit", 1, "2041-03-05").status_code == 404

    # audit: savings actions are logged with the admin as the actor
    actions = run(client, _audit_actions, world["admin"].id)
    assert {"savings_account_created", "savings_account_changed", "savings_account_deleted", "savings_movement_created",
            "savings_movement_changed", "savings_movement_cancelled"} <= actions


async def _audit_actions(user_id):
    async with AsyncSessionLocal() as db:
        return set((await db.execute(select(AuditLog.action).where(AuditLog.actor_user_id == user_id))).scalars().all())


def test_savings_monthly_breakdown(client, world):
    admin = tok(world, "admin")
    start, end = date(2042, 1, 1), date(2042, 3, 31)
    before = client.get(f"/finance/savings/monthly?{period_q(start, end)}", headers=admin).json()
    acc = client.post("/finance/savings/accounts", headers=admin,
                      json={"name": f"Помесячно {RUN}", "kind": "deposit", "opening_balance": 10000, "opening_date": "2041-12-20"}).json()
    for kind, amount, day in (("deposit", 3000, "2042-01-15"), ("withdrawal", 2000, "2042-01-20"), ("deposit", 500, "2042-03-01")):
        assert client.post("/finance/savings/movements", headers=admin,
                           json={"account_id": acc["id"], "kind": kind, "amount": amount, "movement_date": day}).status_code == 201
    after = client.get(f"/finance/savings/monthly?{period_q(start, end)}", headers=admin).json()
    assert [m["month"] for m in after["months"]] == ["2042-01", "2042-02", "2042-03"]
    diff = [(a["deposited"] - b["deposited"], a["withdrawn"] - b["withdrawn"], a["end_balance"] - b["end_balance"])
            for a, b in zip(after["months"], before["months"])]
    assert diff == [(3000, 2000, 11000), (0, 0, 11000), (500, 0, 11500)]
    # a period that starts mid-month: the opening balance includes everything before it
    part = client.get(f"/finance/savings/monthly?{period_q(date(2042, 1, 18), date(2042, 1, 31))}", headers=admin).json()
    full = client.get(f"/finance/savings/monthly?{period_q(date(2042, 1, 1), date(2042, 1, 31))}", headers=admin).json()
    assert part["months"][0]["end_balance"] == full["months"][0]["end_balance"]
    assert part["months"][0]["deposited"] == full["months"][0]["deposited"] - 3000


# ================================================================ migration and deploy
def test_migration_016_is_additive_with_rls():
    from scripts import apply_migration, prod_ops

    sql = (ROOT / "supabase" / "migrations" / "016_savings.sql").read_text(encoding="utf-8")
    assert prod_ops.migration_problems(sql) == []
    assert apply_migration.static_problems(sql) == []
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 2
    assert "BIGINT" in sql and "ON DELETE RESTRICT" in sql


def test_night6_check_files_and_deploy_script():
    from scripts import prod_ops

    checks = prod_ops.parse_checks((ROOT / "scripts" / "prod_postcheck_016.sql").read_text(encoding="utf-8"))
    assert [c["n"] for c in checks] == list(range(1, len(checks) + 1)) and len(checks) >= 5
    assert {"savings_accounts", "savings_movements"} <= set(prod_ops.COUNT_TABLES)
    assert set(prod_ops.FINANCE_TABLES) >= {"payments", "expenses", "recurring_incomes", "recurring_expenses", "recurring_income_records"}
    source = (ROOT / "scripts" / "deploy_night6.py").read_text(encoding="utf-8")
    assert 'MIGRATION = "016_savings.sql"' in source and "night6_smoke(" in source and '"finance"' in source
    out = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "deploy_night6.py")], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert out.returncode == 2 and "--prod" in out.stdout


def test_android_paths_unchanged(client, world):
    """The Android app uses /auth/login, /notifications, /schedule/upcoming and /tasks/* — none changed tonight."""
    admin = tok(world, "admin")
    for url in ("/notifications", "/schedule/upcoming?days=7", "/tasks/projects"):
        assert client.get(url, headers=admin).status_code == 200, url
    assert client.post("/auth/login", json={"email": f"nobody-{RUN}@example.com", "password": "x" * 8},
                       headers={"CF-Connecting-IP": "2001:db8:6::1"}).status_code == 401
