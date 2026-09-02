from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.dependencies import get_current_user, teacher_or_admin
from models.expense import Expense
from models.notification import Notification
from models.payment import Payment
from models.recurring_expense import RecurringExpense
from models.schedule import StudentScheduleSlot
from models.student import Student
from models.user import User
from schemas.finance import (
    ExpenseCreate,
    ExpenseResponse,
    ExpenseUpdate,
    FinanceSummary,
    PaymentCreate,
    PaymentResponse,
    PaymentUpdate,
    RecurringExpenseCreate,
    RecurringExpenseResponse,
    RecurringExpenseUpdate,
    TransactionItem,
)

router = APIRouter(prefix="/finance", tags=["finance"])
LOCAL_TZ = ZoneInfo("Asia/Almaty")


def _local_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=LOCAL_TZ)


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _occurrences(slot: StudentScheduleSlot, start: date, end: date) -> int:
    if end < start or not slot.is_active:
        return 0
    start = max(start, slot.valid_from)
    if slot.valid_until:
        end = min(end, slot.valid_until)
    if end < start:
        return 0
    first = start + timedelta(days=(slot.day_of_week - start.weekday()) % 7)
    if first > end:
        return 0
    return ((end - first).days // 7) + 1


async def _schedule_counts(db: AsyncSession, start: date, end: date) -> tuple[int, Decimal]:
    result = await db.execute(
        select(StudentScheduleSlot, Student.lesson_price)
        .join(Student, Student.id == StudentScheduleSlot.student_id)
        .where(
            Student.is_active.is_(True),
            StudentScheduleSlot.is_active.is_(True),
            StudentScheduleSlot.valid_from <= end,
            (StudentScheduleSlot.valid_until.is_(None) | (StudentScheduleSlot.valid_until >= start)),
        )
    )
    count = 0
    income = Decimal("0.00")
    for slot, price in result.all():
        n = _occurrences(slot, start, end)
        count += n
        income += Decimal(price) * n
    return count, income


async def _sum_payments(db: AsyncSession, start: datetime, end: datetime) -> Decimal:
    value = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.recorded_at >= start,
            Payment.recorded_at < end,
            Payment.is_cancelled.is_(False),
        )
    )
    return Decimal(value or 0)


async def _sum_expenses(db: AsyncSession, start: date, end: date) -> Decimal:
    value = await db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.expense_date >= start,
            Expense.expense_date <= end,
            Expense.is_cancelled.is_(False),
        )
    )
    return Decimal(value or 0)


async def _ensure_recurring_expenses(db: AsyncSession, today: Optional[date] = None) -> int:
    today = today or datetime.now(LOCAL_TZ).date()
    result = await db.execute(select(RecurringExpense).where(RecurringExpense.is_active.is_(True)))
    templates = result.scalars().all()
    created = 0

    for template in templates:
        created_at_local = template.created_at.astimezone(LOCAL_TZ).date() if template.created_at else today
        cursor = _month_start(created_at_local)
        current = _month_start(today)

        while cursor <= current:
            due_day = min(template.day_of_month, monthrange(cursor.year, cursor.month)[1])
            due_date = date(cursor.year, cursor.month, due_day)
            if due_date >= created_at_local and due_date <= today:
                existing = await db.scalar(
                    select(Expense.id).where(
                        Expense.recurring_expense_id == template.id,
                        Expense.recurring_period == cursor,
                    )
                )
                if not existing:
                    db.add(
                        Expense(
                            recurring_expense_id=template.id,
                            recurring_period=cursor,
                            category=template.category,
                            amount=template.amount,
                            description=template.description or template.name,
                            payment_method=template.payment_method,
                            expense_date=due_date,
                        )
                    )
                    created += 1
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

    if created:
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
    return created


@router.post("/payments", response_model=PaymentResponse, status_code=201)
async def create_payment(payload: PaymentCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    if payload.request_id:
        existing = await db.scalar(select(Payment).where(Payment.request_id == payload.request_id))
        if existing:
            return existing

    student = await db.scalar(select(Student).where(Student.id == payload.student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")

    if payload.kaspi_notification_id:
        notification = await db.scalar(select(Notification).where(Notification.id == payload.kaspi_notification_id))
        if not notification:
            raise HTTPException(404, "Kaspi notification not found")
        existing_kaspi = await db.scalar(select(Payment).where(Payment.kaspi_notification_id == payload.kaspi_notification_id))
        if existing_kaspi:
            return existing_kaspi

    payment = Payment(
        student_id=student.id,
        parent_id=payload.parent_id,
        amount=payload.amount,
        payment_method=payload.payment_method,
        kaspi_notification_id=payload.kaspi_notification_id,
        request_id=payload.request_id,
        comment=payload.comment,
        recorded_at=payload.recorded_at or datetime.now(LOCAL_TZ),
    )
    db.add(payment)
    student.balance = Decimal(student.balance) + Decimal(payload.amount)

    if payload.kaspi_notification_id:
        notification = await db.scalar(select(Notification).where(Notification.id == payload.kaspi_notification_id))
        if notification:
            notification.is_processed = True
            notification.action_taken = "payment_recorded"
            notification.processed_at = datetime.now(LOCAL_TZ)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if payload.request_id:
            existing = await db.scalar(select(Payment).where(Payment.request_id == payload.request_id))
            if existing:
                return existing
        if payload.kaspi_notification_id:
            existing = await db.scalar(select(Payment).where(Payment.kaspi_notification_id == payload.kaspi_notification_id))
            if existing:
                return existing
        raise HTTPException(409, "Payment was already recorded") from exc

    await db.refresh(payment)
    return payment


@router.get("/payments", response_model=List[PaymentResponse])
async def list_payments(student_id: Optional[UUID] = None, from_date: Optional[date] = None, to_date: Optional[date] = None, include_cancelled: bool = False, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    query = select(Payment).order_by(Payment.recorded_at.desc())
    if student_id:
        query = query.where(Payment.student_id == student_id)
    if from_date:
        query = query.where(Payment.recorded_at >= _local_start(from_date))
    if to_date:
        query = query.where(Payment.recorded_at < _local_start(to_date + timedelta(days=1)))
    if not include_cancelled:
        query = query.where(Payment.is_cancelled.is_(False))
    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/payments/{payment_id}", response_model=PaymentResponse)
async def update_payment(payment_id: UUID, payload: PaymentUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if not payment:
        raise HTTPException(404, "Payment not found")
    if payment.is_cancelled:
        raise HTTPException(409, "Cancelled payment cannot be edited")

    student = await db.scalar(select(Student).where(Student.id == payment.student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")

    old_amount = Decimal(payment.amount)
    payment.amount = payload.amount
    payment.payment_method = payload.payment_method
    payment.comment = payload.comment
    payment.recorded_at = payload.recorded_at
    student.balance = Decimal(student.balance) + Decimal(payload.amount) - old_amount

    await db.commit()
    await db.refresh(payment)
    return payment


@router.delete("/payments/{payment_id}", response_model=PaymentResponse)
async def cancel_payment(payment_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if not payment:
        raise HTTPException(404, "Payment not found")
    if payment.is_cancelled:
        return payment

    student = await db.scalar(select(Student).where(Student.id == payment.student_id).with_for_update())
    if not student:
        raise HTTPException(404, "Student not found")

    student.balance = Decimal(student.balance) - Decimal(payment.amount)
    payment.is_cancelled = True
    payment.cancelled_at = datetime.now(LOCAL_TZ)
    payment.cancel_reason = "Cancelled from finance"

    await db.commit()
    await db.refresh(payment)
    return payment


@router.post("/expenses", response_model=ExpenseResponse, status_code=201)
async def create_expense(payload: ExpenseCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    expense = Expense(**payload.model_dump())
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


@router.get("/expenses", response_model=List[ExpenseResponse])
async def list_expenses(category: Optional[str] = None, month: Optional[str] = None, include_cancelled: bool = False, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    await _ensure_recurring_expenses(db)
    query = select(Expense).order_by(Expense.expense_date.desc(), Expense.created_at.desc())
    if category:
        query = query.where(Expense.category == category)
    if month:
        try:
            year, month_num = map(int, month.split("-"))
            start = date(year, month_num, 1)
            end = date(year, month_num, monthrange(year, month_num)[1])
        except (ValueError, TypeError):
            raise HTTPException(400, "month must be YYYY-MM")
        query = query.where(Expense.expense_date >= start, Expense.expense_date <= end)
    if not include_cancelled:
        query = query.where(Expense.is_cancelled.is_(False))
    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/expenses/{expense_id}", response_model=ExpenseResponse)
async def update_expense(expense_id: UUID, payload: ExpenseUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    expense = await db.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if not expense:
        raise HTTPException(404, "Expense not found")
    if expense.is_cancelled:
        raise HTTPException(409, "Cancelled expense cannot be edited")

    for key, value in payload.model_dump().items():
        setattr(expense, key, value)

    await db.commit()
    await db.refresh(expense)
    return expense


@router.delete("/expenses/{expense_id}", response_model=ExpenseResponse)
async def cancel_expense(expense_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    expense = await db.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if not expense:
        raise HTTPException(404, "Expense not found")
    if expense.is_cancelled:
        return expense

    expense.is_cancelled = True
    expense.cancelled_at = datetime.now(LOCAL_TZ)
    expense.cancel_reason = "Cancelled from finance"

    await db.commit()
    await db.refresh(expense)
    return expense


@router.get("/recurring-expenses", response_model=List[RecurringExpenseResponse])
async def list_recurring_expenses(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    result = await db.execute(select(RecurringExpense).order_by(RecurringExpense.is_active.desc(), RecurringExpense.created_at.desc()))
    return result.scalars().all()


@router.post("/recurring-expenses", response_model=RecurringExpenseResponse, status_code=201)
async def create_recurring_expense(payload: RecurringExpenseCreate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    item = RecurringExpense(**payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/recurring-expenses/{recurring_id}", response_model=RecurringExpenseResponse)
async def update_recurring_expense(recurring_id: UUID, payload: RecurringExpenseUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    item = await db.scalar(select(RecurringExpense).where(RecurringExpense.id == recurring_id).with_for_update())
    if not item:
        raise HTTPException(404, "Recurring expense not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/recurring-expenses/{recurring_id}", response_model=RecurringExpenseResponse)
async def deactivate_recurring_expense(recurring_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    item = await db.scalar(select(RecurringExpense).where(RecurringExpense.id == recurring_id).with_for_update())
    if not item:
        raise HTTPException(404, "Recurring expense not found")
    item.is_active = False
    await db.commit()
    await db.refresh(item)
    return item


@router.post("/recurring-expenses/sync", response_model=List[ExpenseResponse])
async def sync_recurring_expenses(db: AsyncSession = Depends(get_db), _: User = Depends(teacher_or_admin)):
    await _ensure_recurring_expenses(db)
    result = await db.execute(
        select(Expense).where(Expense.recurring_expense_id.is_not(None)).order_by(Expense.expense_date.desc(), Expense.created_at.desc())
    )
    return result.scalars().all()


@router.get("/summary", response_model=FinanceSummary)
async def finance_summary(year: Optional[int] = None, month: Optional[int] = None, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    await _ensure_recurring_expenses(db)
    today = datetime.now(LOCAL_TZ).date()
    year = year or today.year
    month = month or today.month
    if not 1 <= month <= 12:
        raise HTTPException(400, "month must be 1..12")

    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])
    next_month = month_end + timedelta(days=1)
    income = await _sum_payments(db, _local_start(month_start), _local_start(next_month))
    expense_total = await _sum_expenses(db, month_start, month_end)

    categories = await db.execute(
        select(Expense.category, func.coalesce(func.sum(Expense.amount), 0))
        .where(
            Expense.expense_date >= month_start,
            Expense.expense_date <= month_end,
            Expense.is_cancelled.is_(False),
        )
        .group_by(Expense.category)
    )
    by_category = {category: Decimal(amount or 0) for category, amount in categories.all()}

    all_income = await db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.is_cancelled.is_(False)))
    all_expenses = await db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.is_cancelled.is_(False)))
    current_balance = Decimal(all_income or 0) - Decimal(all_expenses or 0)

    student_balances_total = await db.scalar(
        select(func.coalesce(func.sum(Student.balance), 0)).where(Student.is_active.is_(True), Student.balance > 0)
    )
    negative_balances = await db.scalar(
        select(func.coalesce(func.sum(Student.balance), 0)).where(Student.is_active.is_(True), Student.balance < 0)
    )
    negative_balances_total = abs(Decimal(negative_balances or 0))

    scheduled_count, scheduled_income = await _schedule_counts(db, month_start, month_end)
    forecast_start = max(today, month_start)
    _, forecast_remaining = await _schedule_counts(db, forecast_start, month_end)
    active_students = await db.scalar(select(func.count(Student.id)).where(Student.is_active.is_(True))) or 0

    return FinanceSummary(
        month_start=month_start,
        month_end=month_end,
        income_from_students=income,
        expenses_total=expense_total,
        expenses_by_category=by_category,
        net_profit=income - expense_total,
        total_school_balance=current_balance,
        student_balances_total=Decimal(student_balances_total or 0),
        negative_student_balances_total=negative_balances_total,
        scheduled_lessons_this_month=scheduled_count,
        scheduled_income_this_month=scheduled_income,
        forecast_income_remaining=forecast_remaining,
        active_students=int(active_students),
        monthly_forecast_income=scheduled_income,
    )


@router.get("/transactions", response_model=List[TransactionItem])
async def transactions(limit: int = 100, include_cancelled: bool = False, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    await _ensure_recurring_expenses(db)
    limit = max(1, min(limit, 500))
    payment_query = select(Payment, Student.full_name).join(Student, Student.id == Payment.student_id).order_by(Payment.recorded_at.desc()).limit(limit)
    expense_query = select(Expense).order_by(Expense.expense_date.desc(), Expense.created_at.desc()).limit(limit)
    if not include_cancelled:
        payment_query = payment_query.where(Payment.is_cancelled.is_(False))
        expense_query = expense_query.where(Expense.is_cancelled.is_(False))

    payment_rows = await db.execute(payment_query)
    expense_rows = await db.execute(expense_query)

    items: list[TransactionItem] = []
    for payment, student_name in payment_rows.all():
        items.append(
            TransactionItem(
                id=payment.id,
                operation_type="income",
                amount=payment.amount,
                date=payment.recorded_at,
                student_id=payment.student_id,
                student_name=student_name,
                payment_method=payment.payment_method,
                description=payment.comment,
                is_cancelled=payment.is_cancelled,
            )
        )
    for expense in expense_rows.scalars().all():
        items.append(
            TransactionItem(
                id=expense.id,
                operation_type="expense",
                amount=expense.amount,
                date=_local_start(expense.expense_date),
                category=expense.category,
                payment_method=expense.payment_method,
                description=expense.description,
                is_cancelled=expense.is_cancelled,
                recurring_expense_id=expense.recurring_expense_id,
            )
        )
    return sorted(items, key=lambda item: item.date, reverse=True)[:limit]
