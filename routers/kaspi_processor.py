import re
from datetime import date, timedelta, datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from models.notification import Notification
from models.subscription import Subscription
from models.parent import Parent, StudentParent


async def process_kaspi_payment(notif: Notification, db: AsyncSession) -> tuple[Subscription | None, str | None]:
    """
    Вызывается после сохранения уведомления с detected_action == 'payment_received'.
    Парсит сумму, ищет родителя, обновляет pending абонемент.
    Возвращает (subscription, None) при успехе или (None, "причина") при ошибке.
    """

    # 1. Парсим сумму
    amount = _parse_amount(notif.raw_text) or _parse_amount(notif.ai_summary or "")
    if not amount:
        return None, f"Не удалось распарсить сумму из: {notif.raw_text[:80]}"

    # 2. Определяем имя отправителя
    sender_name = notif.sender_name or _parse_sender_name(notif.raw_text)
    if not sender_name:
        return None, "Не удалось определить имя отправителя"

    # 3. Ищем родителя по имени
    parent = await _find_parent_by_name(sender_name, db)
    if not parent:
        return None, f"Родитель не найден по имени: {sender_name}"

    # 4. Ищем pending абонемент ученика этого родителя
    subscription = await _find_pending_subscription(parent.id, amount, db)
    if not subscription:
        return None, f"Pending абонемент не найден для родителя {parent.full_name}, сумма {amount}"

    # 5. Обновляем абонемент
    subscription.payment_status = "paid"
    subscription.price_paid = amount
    subscription.paid_by_parent_id = parent.id
    subscription.kaspi_transaction_id = str(notif.id)
    subscription.payment_date = datetime.now(timezone.utc)

    if not subscription.valid_from:
        subscription.valid_from = date.today()
    if not subscription.valid_until:
        subscription.valid_until = date.today() + timedelta(days=30)

    # Обновляем уведомление
    notif.related_parent_id = parent.id
    notif.related_student_id = subscription.student_id
    notif.is_processed = True
    notif.processed_at = datetime.now(timezone.utc)
    notif.action_taken = f"subscription_paid:{subscription.id}"
    notif.ai_summary = (notif.ai_summary or "") + f" ✓ Абонемент оплачен (родитель: {parent.full_name})"

    await db.commit()
    await db.refresh(subscription)
    return subscription, None


def _parse_amount(text: str) -> float | None:
    if not text:
        return None
    text = text.replace('\xa0', ' ').replace('\u202f', ' ')
    patterns = [
        r'([\d][\d\s]*[\d])\s*(?:тг|тенге|KZT|₸)',
        r'(?:тг|тенге|KZT|₸)\s*([\d][\d\s]*[\d])',
        r'(\d{4,})',  # просто число от 4 цифр
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            num_str = match.group(1).replace(' ', '')
            try:
                val = float(num_str)
                if val >= 1000:  # фильтр мусора — меньше 1000 тг не абонемент
                    return val
            except ValueError:
                continue
    return None


def _parse_sender_name(text: str) -> str | None:
    """
    Kaspi пишет имя отправителя прямо в тексте:
    "Пополнение 15 000 тг. Денис Ш. Доступно: 32 500 тг."
    "Перевод 5000 тг от Айгуль И. Баланс: ..."
    """
    # Вариант 1: "от Имя Ф." 
    match = re.search(r'от\s+([А-ЯЁа-яёA-Za-z]+(?:\s+[А-ЯЁа-яёA-Za-z]+\.?){0,2})', text)
    if match:
        return match.group(1).strip()

    # Вариант 2: после суммы и точки идёт "Имя Ф." до "Доступно/Баланс"
    # "Пополнение 15 000 тг. Денис Ш. Доступно:"
    match = re.search(
        r'тг\.?\s+([А-ЯЁа-яёA-Za-z]+(?:\s+[А-ЯЁа-яёA-Za-z]{1,2}\.?)?)\s*\.',
        text
    )
    if match:
        name = match.group(1).strip()
        # Фильтруем мусор — "Доступно", "Баланс" и т.д.
        if name.lower() not in ('доступно', 'баланс', 'остаток', 'итого'):
            return name

    return None


async def _find_parent_by_name(name: str, db: AsyncSession) -> Parent | None:
    result = await db.execute(select(Parent))
    parents = result.scalars().all()

    # Разбиваем на части, убираем точки
    name_parts = [p.strip('.').lower() for p in name.split() if len(p.strip('.')) > 1]
    if not name_parts:
        return None

    best: Parent | None = None
    best_score = 0

    for parent in parents:
        full_parts = parent.full_name.lower().split()
        score = 0
        for np in name_parts:
            for fp in full_parts:
                # Точное совпадение или совпадение начала (Ш. → Шевченко)
                if fp == np or fp.startswith(np) or np.startswith(fp[:len(np)]):
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best = parent

    return best if best_score >= 1 else None

async def _find_pending_subscription(parent_id, amount: float, db: AsyncSession) -> Subscription | None:
    # Получаем всех учеников этого родителя
    links_result = await db.execute(
        select(StudentParent).where(StudentParent.parent_id == parent_id)
    )
    student_ids = [link.student_id for link in links_result.scalars().all()]

    if not student_ids:
        return None

    # Ищем pending абонементы
    result = await db.execute(
        select(Subscription).where(
            and_(
                Subscription.student_id.in_(student_ids),
                Subscription.payment_status == "pending"
            )
        ).order_by(Subscription.created_at.desc())
    )
    subs = result.scalars().all()

    if not subs:
        return None

    # Приоритет — совпадение по сумме
    for sub in subs:
        if sub.price_paid and abs(float(sub.price_paid) - amount) < 1:
            return sub

    # Иначе берём последний pending
    return subs[0]