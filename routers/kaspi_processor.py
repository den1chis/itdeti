import re
import logging
from datetime import date, timedelta, datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from models.notification import Notification
from models.subscription import Subscription
from models.parent import Parent, StudentParent

logger = logging.getLogger(__name__)


async def process_kaspi_payment(notif: Notification, db: AsyncSession) -> tuple:
    try:
        # 1. Парсим сумму
        amount = _parse_amount(notif.raw_text)
        if not amount:
            amount = _parse_amount(notif.ai_summary or "")
        if not amount:
            return None, f"Не удалось распарсить сумму из: {notif.raw_text[:80]}"
        logger.info(f"Kaspi: сумма = {amount}")

        # 2. Имя ВСЕГДА из raw_text — sender_name у Kaspi всегда "Kaspi Bank"
        sender_name = _parse_sender_name(notif.raw_text)
        if not sender_name:
            return None, f"Не удалось найти имя в тексте: {notif.raw_text[:80]}"
        logger.info(f"Kaspi: отправитель = {sender_name}")

        # 3. Ищем родителя
        parent, score = await _find_parent_by_name(sender_name, db)
        if not parent:
            return None, f"Родитель не найден по имени: {sender_name}"
        logger.info(f"Kaspi: найден родитель = {parent.full_name} (score={score})")

        # 4. Ищем pending абонемент
        subscription = await _find_pending_subscription(parent.id, amount, db)
        if not subscription:
            return None, f"Pending абонемент не найден для родителя {parent.full_name}"
        logger.info(f"Kaspi: найден абонемент = {subscription.id}")

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

        # 6. Обновляем уведомление
        notif.related_parent_id = parent.id
        notif.related_student_id = subscription.student_id
        notif.is_processed = True
        notif.processed_at = datetime.now(timezone.utc)
        notif.action_taken = f"subscription_paid:{subscription.id}"
        notif.ai_summary = (notif.ai_summary or "") + f" ✓ Оплачен (родитель: {parent.full_name})"

        await db.commit()
        await db.refresh(subscription)
        return subscription, None

    except Exception as e:
        logger.error(f"process_kaspi_payment exception: {e}", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return None, f"Exception: {str(e)}"


def _parse_amount(text: str) -> float | None:
    if not text:
        return None
    # Нормализуем пробелы
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u00a0', ' ')

    patterns = [
        # "15 000 тг" или "15000 тг"
        r'([\d][\d\s]{2,}[\d])\s*(?:тг|тенге|KZT|₸)',
        # "тг 15000"
        r'(?:тг|тенге|KZT|₸)\s*([\d][\d\s]*[\d])',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            num_str = re.sub(r'\s+', '', match.group(1))
            try:
                val = float(num_str)
                if val >= 500:
                    return val
            except ValueError:
                continue
    return None


def _parse_sender_name(text: str) -> str | None:
    if not text:
        return None

    # Вариант 1: "от Имя Ф."
    match = re.search(
        r'от\s+([А-ЯЁа-яёA-Za-z]+(?:\s+[А-ЯЁа-яёA-Za-z]{1,15}\.?){0,2})',
        text
    )
    if match:
        name = match.group(1).strip().rstrip('.')
        if _is_valid_name(name):
            return name

    # Вариант 2: "тг. Имя Ф. Доступно/Баланс"
    # "Пополнение 15 000 тг. Александр Ш. Доступно: 32 500 тг."
    match = re.search(
        r'тг\.?\s+([А-ЯЁа-яёA-Za-z]+(?:\s+[А-ЯЁа-яёA-Za-z]{1,3}\.?)?)\s*[.\-,]?\s*(?:Доступно|Баланс|Остаток|$)',
        text,
        re.IGNORECASE
    )
    if match:
        name = match.group(1).strip().rstrip('.')
        if _is_valid_name(name):
            return name

    # Вариант 3: просто берём слово после "тг." которое начинается с заглавной
    match = re.search(r'тг\.?\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁа-яёA-Za-z]{1,3}\.?)?)', text)
    if match:
        name = match.group(1).strip().rstrip('.')
        if _is_valid_name(name):
            return name

    return None


STOP_WORDS = {
    'доступно', 'баланс', 'остаток', 'итого', 'сумма', 'kaspi',
    'bank', 'банк', 'перевод', 'пополнение', 'оплата', 'зачисление'
}

def _is_valid_name(name: str) -> bool:
    if not name or len(name) < 2:
        return False
    if name.lower().split()[0] in STOP_WORDS:
        return False
    return True


async def _find_parent_by_name(name: str, db: AsyncSession) -> tuple:
    """Возвращает (Parent | None, score)"""
    result = await db.execute(select(Parent))
    parents = result.scalars().all()

    if not parents:
        return None, 0

    # Части имени из уведомления, без точек
    name_parts = [p.strip('.').lower() for p in name.split() if len(p.strip('.')) >= 2]
    if not name_parts:
        return None, 0

    best: Parent | None = None
    best_score = 0

    for parent in parents:
        db_parts = parent.full_name.lower().split()
        score = 0
        for np in name_parts:
            for dp in db_parts:
                dp_clean = dp.strip('.')
                np_clean = np.strip('.')
                # Точное совпадение
                if dp_clean == np_clean:
                    score += 2
                    break
                # Сокращение: "Ш" или "Ш." совпадает с "Шевченко"
                elif len(np_clean) == 1 and dp_clean.startswith(np_clean):
                    score += 1
                    break
                elif len(dp_clean) == 1 and np_clean.startswith(dp_clean):
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best = parent

    return (best, best_score) if best_score >= 1 else (None, 0)


async def _find_pending_subscription(parent_id, amount: float, db: AsyncSession) -> Subscription | None:
    # Ученики этого родителя
    links_result = await db.execute(
        select(StudentParent).where(StudentParent.parent_id == parent_id)
    )
    student_ids = [link.student_id for link in links_result.scalars().all()]

    if not student_ids:
        logger.warning(f"_find_pending_subscription: у родителя {parent_id} нет учеников")
        return None

    # Pending абонементы
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
        logger.warning(f"_find_pending_subscription: нет pending абонементов для учеников {student_ids}")
        return None

    # Приоритет 1 — совпадение по сумме (±1 тг)
    for sub in subs:
        if sub.price_paid is not None and abs(float(sub.price_paid) - amount) < 1:
            return sub

    # Приоритет 2 — последний pending
    return subs[0]