import re
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from core.database import get_db
from core.dependencies import get_current_user
from models.notification import Notification
from models.user import User
import uuid

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────────

class NotificationIn(BaseModel):
    source: str
    raw_text: str
    sender_name: Optional[str] = None
    sender_phone: Optional[str] = None


class NotificationResponse(BaseModel):
    id: uuid.UUID
    source: str
    raw_text: str
    sender_name: Optional[str]
    sender_phone: Optional[str]
    detected_action: str
    ai_summary: str
    requires_confirmation: bool
    received_at: datetime

    class Config:
        from_attributes = True


class NotificationListItem(BaseModel):
    id: uuid.UUID
    source: str
    detected_action: str
    ai_summary: Optional[str]
    is_processed: bool
    requires_confirmation: bool
    received_at: datetime

    class Config:
        from_attributes = True


# ── Kaspi parser ─────────────────────────────────────────────

KASPI_PATTERNS = [
    (r"[Пп]ополнение\s+([\d\s]+)\s*тг", "payment_received", "Пополнение"),
    (r"[Пп]еревод\s+([\d\s]+)\s*тг", "payment_received", "Перевод"),
    (r"[Оо]плата\s+([\d\s]+)\s*тг", "payment_received", "Оплата"),
    (r"[Зз]ачисление\s+([\d\s]+)\s*тг", "payment_received", "Зачисление"),
]

RESCHEDULE_KEYWORDS = [
    "перенес", "перенос", "перенести", "другое время", "другой день",
    "не смогу", "не сможем", "не придёт", "не придет", "пропустит",
    "reschedule", "cancel", "отмен",
]

CONFIRMATION_KEYWORDS = [
    "придём", "придем", "будем", "подтверждаем", "да", "ок", "окей",
    "хорошо", "договорились", "приду", "буду",
]


def parse_kaspi(text: str) -> dict:
    for pattern, action, label in KASPI_PATTERNS:
        match = re.search(pattern, text)
        if match:
            amount_str = match.group(1).replace(" ", "").replace("\xa0", "")
            try:
                amount = int(amount_str)
            except ValueError:
                amount = None
            summary = f"{label} {amount:,} тг".replace(",", " ") if amount else label
            return {
                "detected_action": action,
                "ai_summary": summary,
                "ai_confidence": 0.9,
                "requires_confirmation": False,
            }
    return {
        "detected_action": "unknown",
        "ai_summary": "Уведомление Каспи — не распознано",
        "ai_confidence": 0.3,
        "requires_confirmation": True,
    }


def parse_whatsapp(text: str, sender: Optional[str]) -> dict:
    text_lower = text.lower()
    for kw in RESCHEDULE_KEYWORDS:
        if kw in text_lower:
            who = sender or "Родитель"
            return {
                "detected_action": "reschedule_request",
                "ai_summary": f"{who} просит перенести занятие — требует подтверждения",
                "ai_confidence": 0.75,
                "requires_confirmation": True,
            }
    for kw in CONFIRMATION_KEYWORDS:
        if kw in text_lower:
            who = sender or "Родитель"
            return {
                "detected_action": "message",
                "ai_summary": f"{who} подтвердил(а) — '{text[:60]}'",
                "ai_confidence": 0.7,
                "requires_confirmation": False,
            }
    who = sender or "Неизвестный"
    return {
        "detected_action": "message",
        "ai_summary": f"Сообщение от {who} — требует внимания: '{text[:80]}'",
        "ai_confidence": 0.5,
        "requires_confirmation": True,
    }


def parse_notification(source: str, text: str, sender: Optional[str]) -> dict:
    if source == "kaspi":
        return parse_kaspi(text)
    elif source in ("whatsapp", "sms"):
        return parse_whatsapp(text, sender)
    return {
        "detected_action": "unknown",
        "ai_summary": f"Уведомление из {source} — требует внимания",
        "ai_confidence": 0.3,
        "requires_confirmation": True,
    }


# ── Endpoints ─────────────────────────────────────────────────

@router.post("", response_model=NotificationResponse, status_code=201)
async def receive_notification(
    payload: NotificationIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    parsed = parse_notification(payload.source, payload.raw_text, payload.sender_name)

    notif = Notification(
        source=payload.source,
        raw_text=payload.raw_text,
        sender_name=payload.sender_name,
        sender_phone=payload.sender_phone,
        detected_action=parsed["detected_action"],
        ai_summary=parsed["ai_summary"],
        ai_confidence=parsed["ai_confidence"],
        requires_confirmation=parsed["requires_confirmation"],
        is_processed=not parsed["requires_confirmation"],
        processed_at=datetime.now(timezone.utc) if not parsed["requires_confirmation"] else None,
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    # ── Автоматическая обработка Kaspi-оплаты ──
    if notif.detected_action == "payment_received":
        try:
            from routers.kaspi_processor import process_kaspi_payment
            sub, error = await process_kaspi_payment(notif, db)
            if error:
                logger.warning(f"Kaspi processor: {error}")
            else:
                logger.info(f"Kaspi processor: абонемент {sub.id} обновлён")
        except Exception as e:
            logger.error(f"Kaspi processor exception: {e}", exc_info=True)
            # Не падаем — уведомление уже сохранено

    await db.refresh(notif)
    return notif


@router.get("", response_model=List[NotificationListItem])
async def list_notifications(
    unprocessed_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Notification).order_by(Notification.received_at.desc())
    if unprocessed_only:
        q = q.where(Notification.is_processed == False)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/{notification_id}/confirm")
async def confirm_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from fastapi import HTTPException
    result = await db.execute(select(Notification).where(Notification.id == notification_id))
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_processed = True
    notif.confirmed_at = datetime.now(timezone.utc)
    notif.processed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"detail": "Confirmed", "id": str(notification_id)}