"""Night 6: the new Финансы page — summary for any period, income forecast for next month, savings.

Separate module on purpose: routers/finance.py (and every answer of its routes, used by the CRM dashboard and
the old page) is not changed. Admin only, like the whole /finance router (core.access.require_admin).

Period: ?from=YYYY-MM-DD&to=YYYY-MM-DD (both days included, Asia/Almaty); without them — the current month.
Savings amounts are whole tenge. The balance of an account is the sum of its movements that are not cancelled;
it never goes below zero (a withdrawal, an edit or a cancellation that would make it negative -> 409).
An account with movements is never deleted (archive instead -> 409 on DELETE)."""
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.access import require_admin
from core.client_ip import client_ip
from core.database import get_db
from models.expense import Expense
from models.payment import Payment
from models.recurring_income import RecurringIncome, RecurringIncomeRecord
from models.savings import SavingsAccount, SavingsMovement
from models.student import Student
from models.user import User
from routers.finance import LOCAL_TZ, _local_start, _schedule_counts, _sum_expenses, _sum_payments, _sum_recurring_income
from schemas.finance import TransactionItem
from schemas.finance_v2 import (
    MAX_DATE,
    MIN_DATE,
    AverageForecast,
    AverageMonth,
    Forecast,
    ForecastItem,
    PeriodSummary,
    SavingsAccountCreate,
    SavingsAccountResponse,
    SavingsAccountUpdate,
    SavingsMonth,
    SavingsMonthly,
    SavingsMovementCreate,
    SavingsMovementResponse,
    SavingsMovementUpdate,
    SavingsOverview,
    ScheduleForecast,
)

router = APIRouter(prefix="/finance", tags=["finance"], dependencies=[Depends(require_admin)])

MAX_PERIOD_DAYS = 3700  # about ten years: enough for any report, bounded work per request
OPERATIONS_LIMIT = 2000
AVERAGE_MONTHS = 3


# ---------------------------------------------------------------- helpers
def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _month_bounds(value: date) -> tuple[date, date]:
    return date(value.year, value.month, 1), date(value.year, value.month, monthrange(value.year, value.month)[1])


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def period(date_from: Optional[date] = Query(None, alias="from"), date_to: Optional[date] = Query(None, alias="to")) -> tuple[date, date]:
    """?from&to -> (from, to). None of them -> the current month; one of them -> 400."""
    if date_from is None and date_to is None:
        return _month_bounds(_today())
    if date_from is None or date_to is None:
        raise HTTPException(400, "укажите обе даты периода: from и to")
    if not (MIN_DATE <= date_from <= MAX_DATE and MIN_DATE <= date_to <= MAX_DATE):
        raise HTTPException(400, "даты периода — с 2000 по 2100 год")
    if date_to < date_from:
        raise HTTPException(400, "конец периода раньше начала")
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        raise HTTPException(400, "период слишком длинный")
    return date_from, date_to


def _signed():
    return case((SavingsMovement.kind == "deposit", SavingsMovement.amount), else_=-SavingsMovement.amount)


async def _balance(db: AsyncSession, account_id: UUID) -> int:
    value = await db.scalar(
        select(func.coalesce(func.sum(_signed()), 0)).where(
            SavingsMovement.account_id == account_id, SavingsMovement.is_cancelled.is_(False))
    )
    return int(value or 0)


async def _locked_account(db: AsyncSession, account_id: UUID) -> SavingsAccount:
    """The account row locked FOR UPDATE: movements of one account are changed one at a time, so two
    simultaneous withdrawals cannot both pass the "balance stays >= 0" check."""
    account = await db.scalar(select(SavingsAccount).where(SavingsAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Счёт не найден")
    return account


def _movement_response(movement: SavingsMovement, account_name: str) -> SavingsMovementResponse:
    data = SavingsMovementResponse.model_validate(movement)
    data.account_name = account_name
    return data


# ---------------------------------------------------------------- period summary and operations
@router.get("/period", response_model=PeriodSummary)
async def period_summary(bounds: tuple = Depends(period), db: AsyncSession = Depends(get_db)):
    """Totals of the period. The formulas are the ones of GET /finance/summary (same helpers), so for a whole
    month the numbers equal the old page: payments by local day of recorded_at, recurring income records by
    received_date, expenses by expense_date; cancelled rows are not counted."""
    start, end = bounds
    students = await _sum_payments(db, _local_start(start), _local_start(end + timedelta(days=1)))
    recurring = await _sum_recurring_income(db, start, end)
    expenses = await _sum_expenses(db, start, end)
    categories = await db.execute(
        select(Expense.category, func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= start, Expense.expense_date <= end, Expense.is_cancelled.is_(False))
        .group_by(Expense.category)
    )
    total = students + recurring
    return PeriodSummary(
        date_from=start, date_to=end, total_income=total, income_from_students=students,
        recurring_income_total=recurring, expenses_total=expenses, net=total - expenses,
        expenses_by_category={category: Decimal(amount or 0) for category, amount in categories.all()},
    )


@router.get("/period/operations", response_model=List[TransactionItem])
async def period_operations(bounds: tuple = Depends(period), include_cancelled: bool = False, db: AsyncSession = Depends(get_db)):
    """History of the period: student payments, recurring income records and expenses (same items as
    GET /finance/transactions, which stays as it is), newest first."""
    start, end = bounds
    payments = (select(Payment, Student.full_name).join(Student, Student.id == Payment.student_id)
                .where(Payment.recorded_at >= _local_start(start), Payment.recorded_at < _local_start(end + timedelta(days=1)))
                .order_by(Payment.recorded_at.desc()).limit(OPERATIONS_LIMIT))
    expenses = (select(Expense).where(Expense.expense_date >= start, Expense.expense_date <= end)
                .order_by(Expense.expense_date.desc(), Expense.created_at.desc()).limit(OPERATIONS_LIMIT))
    records = (select(RecurringIncomeRecord, RecurringIncome.name)
               .outerjoin(RecurringIncome, RecurringIncome.id == RecurringIncomeRecord.recurring_income_id)
               .where(RecurringIncomeRecord.received_date >= start, RecurringIncomeRecord.received_date <= end)
               .order_by(RecurringIncomeRecord.received_date.desc(), RecurringIncomeRecord.created_at.desc()).limit(OPERATIONS_LIMIT))
    if not include_cancelled:
        payments = payments.where(Payment.is_cancelled.is_(False))
        expenses = expenses.where(Expense.is_cancelled.is_(False))
        records = records.where(RecurringIncomeRecord.is_cancelled.is_(False))
    items: list[TransactionItem] = []
    for payment, student_name in (await db.execute(payments)).all():
        items.append(TransactionItem(
            id=payment.id, operation_type="income", amount=payment.amount, date=payment.recorded_at,
            student_id=payment.student_id, student_name=student_name, payment_method=payment.payment_method,
            description=payment.comment, is_cancelled=payment.is_cancelled, source_type="student_payment"))
    for record, name in (await db.execute(records)).all():
        items.append(TransactionItem(
            id=record.id, operation_type="income", amount=record.amount, date=_local_start(record.received_date),
            payment_method=record.payment_method, description=record.comment or name, category=name,
            is_cancelled=record.is_cancelled, recurring_income_id=record.recurring_income_id, source_type="recurring_income"))
    for expense in (await db.execute(expenses)).scalars().all():
        items.append(TransactionItem(
            id=expense.id, operation_type="expense", amount=expense.amount, date=_local_start(expense.expense_date),
            category=expense.category, payment_method=expense.payment_method, description=expense.description,
            is_cancelled=expense.is_cancelled, recurring_expense_id=expense.recurring_expense_id,
            source_type="recurring_expense" if expense.recurring_expense_id else "expense"))
    return sorted(items, key=lambda item: item.date, reverse=True)[:OPERATIONS_LIMIT]


# ---------------------------------------------------------------- forecast
@router.get("/forecast", response_model=Forecast)
async def forecast(db: AsyncSession = Depends(get_db)):
    """Income forecast for NEXT month, two ways (the page shows one, switchable):
    schedule — weekly schedule slots of active students in next month x each student's lesson price
               (the formula of "scheduled income" on the old page) + every active recurring income;
    average  — mean income (student payments + recurring income records) of the last 3 FULL months."""
    today = _today()
    next_start, next_end = _month_bounds(_add_months(today, 1))
    lessons, students_income = await _schedule_counts(db, next_start, next_end)
    recurring_rows = (await db.execute(
        select(RecurringIncome.name, RecurringIncome.amount).where(RecurringIncome.is_active.is_(True))
        .order_by(RecurringIncome.name))).all()
    recurring_items = [ForecastItem(name=name, amount=Decimal(amount or 0)) for name, amount in recurring_rows]
    recurring = sum((item.amount for item in recurring_items), Decimal("0.00"))
    schedule = ScheduleForecast(total=students_income + recurring, students_income=students_income, lessons_count=lessons,
                                recurring_income=recurring, recurring_items=recurring_items)

    months = []
    for back in range(AVERAGE_MONTHS, 0, -1):
        start, end = _month_bounds(_add_months(today, -back))
        paid = await _sum_payments(db, _local_start(start), _local_start(end + timedelta(days=1)))
        received = await _sum_recurring_income(db, start, end)
        months.append(AverageMonth(month=f"{start:%Y-%m}", students_income=paid, recurring_income=received, total=paid + received))
    count = Decimal(AVERAGE_MONTHS)
    q = Decimal("0.01")
    avg_students = (sum((m.students_income for m in months), Decimal("0")) / count).quantize(q)
    avg_recurring = (sum((m.recurring_income for m in months), Decimal("0")) / count).quantize(q)
    average = AverageForecast(total=avg_students + avg_recurring, students_income=avg_students,
                              recurring_income=avg_recurring, months=months)
    return Forecast(month_start=next_start, month_end=next_end, schedule=schedule, average=average)


# ---------------------------------------------------------------- savings: accounts
@router.get("/savings", response_model=SavingsOverview)
async def savings_overview(db: AsyncSession = Depends(get_db)):
    """All accounts with their CURRENT balance (independent of any period) and the total."""
    rows = (await db.execute(
        select(SavingsAccount,
               func.coalesce(func.sum(case((SavingsMovement.is_cancelled.is_(False), _signed()), else_=0)), 0),
               func.count(SavingsMovement.id))
        .outerjoin(SavingsMovement, SavingsMovement.account_id == SavingsAccount.id)
        .group_by(SavingsAccount.id)
        .order_by(SavingsAccount.is_archived, SavingsAccount.kind.desc(), SavingsAccount.created_at)
    )).all()
    accounts = [SavingsAccountResponse(id=a.id, name=a.name, kind=a.kind, is_archived=a.is_archived, balance=int(balance),
                                       movements_count=int(count), created_at=a.created_at) for a, balance, count in rows]
    return SavingsOverview(total=sum(a.balance for a in accounts), accounts=accounts)


@router.post("/savings/accounts", response_model=SavingsAccountResponse, status_code=201)
async def create_savings_account(payload: SavingsAccountCreate, request: Request, db: AsyncSession = Depends(get_db),
                                 user: User = Depends(require_admin)):
    account = SavingsAccount(name=payload.name, kind=payload.kind)
    db.add(account)
    await db.flush()
    count = 0
    if payload.opening_balance > 0:
        db.add(SavingsMovement(account_id=account.id, kind="deposit", amount=payload.opening_balance,
                               movement_date=payload.opening_date or _today(), comment="Начальный остаток",
                               created_by=user.id))
        count = 1
    await db.commit()
    await db.refresh(account)
    await audit.record(audit.SAVINGS_ACCOUNT_CREATED, actor=user, target_type="savings_account", target_id=account.id,
                       ip=client_ip(request), details={"name": account.name, "kind": account.kind,
                                                       "opening_balance": payload.opening_balance})
    return SavingsAccountResponse(id=account.id, name=account.name, kind=account.kind, is_archived=account.is_archived,
                                  balance=payload.opening_balance, movements_count=count, created_at=account.created_at)


@router.patch("/savings/accounts/{account_id}", response_model=SavingsAccountResponse)
async def update_savings_account(account_id: UUID, payload: SavingsAccountUpdate, request: Request,
                                 db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    account = await _locked_account(db, account_id)
    changes = {}
    if payload.name is not None and payload.name != account.name:
        changes["name"] = f"{account.name} -> {payload.name}"
        account.name = payload.name
    if payload.is_archived is not None and payload.is_archived != account.is_archived:
        changes["archived"] = payload.is_archived
        account.is_archived = payload.is_archived
        account.archived_at = datetime.now(LOCAL_TZ) if payload.is_archived else None
    await db.commit()
    await db.refresh(account)
    if changes:
        await audit.record(audit.SAVINGS_ACCOUNT_CHANGED, actor=user, target_type="savings_account", target_id=account.id,
                           ip=client_ip(request), details=changes)
    count = await db.scalar(select(func.count(SavingsMovement.id)).where(SavingsMovement.account_id == account.id))
    return SavingsAccountResponse(id=account.id, name=account.name, kind=account.kind, is_archived=account.is_archived,
                                  balance=await _balance(db, account.id), movements_count=int(count or 0),
                                  created_at=account.created_at)


@router.delete("/savings/accounts/{account_id}", status_code=204)
async def delete_savings_account(account_id: UUID, request: Request, db: AsyncSession = Depends(get_db),
                                 user: User = Depends(require_admin)):
    """Only an account without any movement (even a cancelled one) is deleted; otherwise 409 — archive it."""
    account = await _locked_account(db, account_id)
    used = await db.scalar(select(func.count(SavingsMovement.id)).where(SavingsMovement.account_id == account.id))
    if used:
        raise HTTPException(409, "У счёта есть движения — его можно только архивировать")
    name = account.name
    await db.delete(account)
    await db.commit()
    await audit.record(audit.SAVINGS_ACCOUNT_DELETED, actor=user, target_type="savings_account", target_id=account_id,
                       ip=client_ip(request), details={"name": name})


# ---------------------------------------------------------------- savings: movements
@router.get("/savings/movements", response_model=List[SavingsMovementResponse])
async def list_savings_movements(bounds: tuple = Depends(period), account_id: Optional[UUID] = None,
                                 include_cancelled: bool = False, db: AsyncSession = Depends(get_db)):
    start, end = bounds
    query = (select(SavingsMovement, SavingsAccount.name).join(SavingsAccount, SavingsAccount.id == SavingsMovement.account_id)
             .where(SavingsMovement.movement_date >= start, SavingsMovement.movement_date <= end)
             .order_by(SavingsMovement.movement_date.desc(), SavingsMovement.created_at.desc()).limit(OPERATIONS_LIMIT))
    if account_id:
        query = query.where(SavingsMovement.account_id == account_id)
    if not include_cancelled:
        query = query.where(SavingsMovement.is_cancelled.is_(False))
    return [_movement_response(m, name) for m, name in (await db.execute(query)).all()]


@router.post("/savings/movements", response_model=SavingsMovementResponse, status_code=201)
async def create_savings_movement(payload: SavingsMovementCreate, request: Request, db: AsyncSession = Depends(get_db),
                                  user: User = Depends(require_admin)):
    account = await _locked_account(db, payload.account_id)
    if account.is_archived:
        raise HTTPException(409, "Счёт в архиве: сначала верните его из архива")
    if payload.kind == "withdrawal" and await _balance(db, account.id) - payload.amount < 0:
        raise HTTPException(409, "Нельзя снять больше, чем есть на счёте")
    movement = SavingsMovement(account_id=account.id, kind=payload.kind, amount=payload.amount,
                               movement_date=payload.movement_date, comment=payload.comment, created_by=user.id)
    db.add(movement)
    await db.commit()
    await db.refresh(movement)
    await audit.record(audit.SAVINGS_MOVEMENT_CREATED, actor=user, target_type="savings_movement", target_id=movement.id,
                       ip=client_ip(request), details={"account": account.name, "kind": movement.kind, "amount": movement.amount,
                                                       "date": str(movement.movement_date)})
    return _movement_response(movement, account.name)


async def _locked_movement(db: AsyncSession, movement_id: UUID) -> tuple[SavingsMovement, SavingsAccount]:
    """Lock the account first, then the movement (one order everywhere: no deadlock with create)."""
    account_id = await db.scalar(select(SavingsMovement.account_id).where(SavingsMovement.id == movement_id))
    if not account_id:
        raise HTTPException(404, "Движение не найдено")
    account = await _locked_account(db, account_id)
    movement = await db.scalar(select(SavingsMovement).where(SavingsMovement.id == movement_id).with_for_update())
    if not movement:
        raise HTTPException(404, "Движение не найдено")
    return movement, account


@router.patch("/savings/movements/{movement_id}", response_model=SavingsMovementResponse)
async def update_savings_movement(movement_id: UUID, payload: SavingsMovementUpdate, request: Request,
                                  db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    movement, account = await _locked_movement(db, movement_id)
    if movement.is_cancelled:
        raise HTTPException(409, "Отменённое движение нельзя изменить")
    if account.is_archived:
        raise HTTPException(409, "Счёт в архиве: сначала верните его из архива")
    old_signed = movement.amount if movement.kind == "deposit" else -movement.amount
    new_signed = payload.amount if payload.kind == "deposit" else -payload.amount
    if await _balance(db, account.id) - old_signed + new_signed < 0:
        raise HTTPException(409, "После изменения остаток счёта стал бы меньше нуля")
    before = f"{movement.kind} {movement.amount} {movement.movement_date}"
    movement.kind, movement.amount = payload.kind, payload.amount
    movement.movement_date, movement.comment = payload.movement_date, payload.comment
    await db.commit()
    await db.refresh(movement)
    await audit.record(audit.SAVINGS_MOVEMENT_CHANGED, actor=user, target_type="savings_movement", target_id=movement.id,
                       ip=client_ip(request), details={"account": account.name, "before": before,
                                                       "after": f"{movement.kind} {movement.amount} {movement.movement_date}"})
    return _movement_response(movement, account.name)


@router.delete("/savings/movements/{movement_id}", response_model=SavingsMovementResponse)
async def cancel_savings_movement(movement_id: UUID, request: Request, db: AsyncSession = Depends(get_db),
                                  user: User = Depends(require_admin)):
    """Cancels (is_cancelled), never deletes — like payments and expenses."""
    movement, account = await _locked_movement(db, movement_id)
    if movement.is_cancelled:
        return _movement_response(movement, account.name)
    if movement.kind == "deposit" and await _balance(db, account.id) - movement.amount < 0:
        raise HTTPException(409, "После отмены остаток счёта стал бы меньше нуля: сначала отмените снятие")
    movement.is_cancelled = True
    movement.cancelled_at = datetime.now(LOCAL_TZ)
    await db.commit()
    await db.refresh(movement)
    await audit.record(audit.SAVINGS_MOVEMENT_CANCELLED, actor=user, target_type="savings_movement", target_id=movement.id,
                       ip=client_ip(request), details={"account": account.name, "kind": movement.kind, "amount": movement.amount,
                                                       "date": str(movement.movement_date)})
    return _movement_response(movement, account.name)


@router.get("/savings/monthly", response_model=SavingsMonthly)
async def savings_monthly(bounds: tuple = Depends(period), db: AsyncSession = Depends(get_db)):
    """Per calendar month touched by the period: deposited and withdrawn within the period, and the total
    balance of all accounts at the end of the month (or at the end of the period for its last month)."""
    start, end = bounds
    opening = int(await db.scalar(select(func.coalesce(func.sum(_signed()), 0)).where(
        SavingsMovement.is_cancelled.is_(False), SavingsMovement.movement_date < start)) or 0)
    month_key = func.to_char(SavingsMovement.movement_date, "YYYY-MM")
    rows = (await db.execute(
        select(month_key,
               func.coalesce(func.sum(case((SavingsMovement.kind == "deposit", SavingsMovement.amount), else_=0)), 0),
               func.coalesce(func.sum(case((SavingsMovement.kind == "withdrawal", SavingsMovement.amount), else_=0)), 0))
        .where(SavingsMovement.is_cancelled.is_(False), SavingsMovement.movement_date >= start, SavingsMovement.movement_date <= end)
        .group_by(month_key)
    )).all()
    by_month = {key: (int(dep), int(wd)) for key, dep, wd in rows}
    months, balance, cursor = [], opening, date(start.year, start.month, 1)
    while cursor <= end:
        key = f"{cursor:%Y-%m}"
        deposited, withdrawn = by_month.get(key, (0, 0))
        balance += deposited - withdrawn
        months.append(SavingsMonth(month=key, deposited=deposited, withdrawn=withdrawn, end_balance=balance))
        cursor = _add_months(cursor, 1)
    return SavingsMonthly(date_from=start, date_to=end, months=months)
