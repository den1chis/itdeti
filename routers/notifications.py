import re
from datetime import datetime, timezone
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import get_current_user, teacher_or_admin
from models.notification import Notification
from models.user import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationIn(BaseModel):
    source: str
    raw_text: str
    sender_name: Optional[str] = None
    sender_phone: Optional[str] = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    raw_text: str
    sender_name: Optional[str]
    sender_phone: Optional[str]
    detected_action: str
    ai_summary: Optional[str]
    requires_confirmation: bool
    received_at: datetime


class NotificationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    detected_action: str
    ai_summary: Optional[str]
    is_processed: bool
    requires_confirmation: bool
    received_at: datetime


KASPI_PATTERNS = [
    (r"[Пп]ополнение\s+([\d\s]+)\s*тг", "Пополнение"),
    (r"[Пп]еревод\s+([\d\s]+)\s*тг", "Перевод"),
    (r"[Оо]плата\s+([\d\s]+)\s*тг", "Оплата"),
    (r"[Зз]ачисление\s+([\d\s]+)\s*тг", "Зачисление"),
]

RESCHEDULE_KEYWORDS = (
    "перенес", "перенос", "перенести", "другое время", "другой день",
    "не смогу", "не сможем", "не придёт", "не придет", "пропустит",
    "reschedule", "cancel", "отмен",
)


def parse_notification(source: str, text: str, sender: Optional[str]) -> dict:
    lowered = text.lower()
    if source == "kaspi":
        for pattern, label in KASPI_PATTERNS:
            match = re.search(pattern, text)
            if match:
                amount = int(match.group(1).replace(" ", "").replace("\xa0", ""))
                return {
                    "detected_action": "payment_received",
                    "ai_summary": f"{label}: {amount:,}".replace(",", " ") + " тг",
                    "ai_confidence": 0.9,
                    "requires_confirmation": True,
                }
        return {
            "detected_action": "payment_received",
            "ai_summary": "Kaspi уведомление распознано, но сумма не извлечена",
            "ai_confidence": 0.3,
            "requires_confirmation": True,
        }

    if source in ("whatsapp", "sms"):
        for keyword in RESCHEDULE_KEYWORDS:
            if keyword in lowered:
                return {
                    "detected_action": "reschedule_request",
                    "ai_summary": f"{sender or 'Контакт'} просит перенести занятие",
                    "ai_confidence": 0.75,
                    "requires_confirmation": True,
                }
        return {
            "detected_action": "message",
            "ai_summary": f"Сообщение от {sender or 'неизвестного контакта'}",
            "ai_confidence": 0.6,
            "requires_confirmation": True,
        }

    return {
        "detected_action": "unknown",
        "ai_summary": f"Уведомление из {source}",
        "ai_confidence": 0.2,
        "requires_confirmation": True,
    }


@router.post("", response_model=NotificationResponse, status_code=201)
async def receive_notification(
    payload: NotificationIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    parsed = parse_notification(payload.source, payload.raw_text, payload.sender_name)
    notification = Notification(
        source=payload.source,
        raw_text=payload.raw_text,
        sender_name=payload.sender_name,
        sender_phone=payload.sender_phone,
        detected_action=parsed["detected_action"],
        ai_summary=parsed["ai_summary"],
        ai_confidence=parsed["ai_confidence"],
        requires_confirmation=parsed["requires_confirmation"],
        is_processed=False,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


@router.get("", response_model=List[NotificationListItem])
async def list_notifications(
    unprocessed_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Notification).order_by(Notification.received_at.desc())
    if unprocessed_only:
        query = query.where(Notification.is_processed.is_(False))
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/{notification_id}/confirm")
async def confirm_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(teacher_or_admin),
):
    notification = await db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update())
    if not notification:
        raise HTTPException(404, "Notification not found")
    notification.is_processed = True
    notification.confirmed_at = datetime.now(timezone.utc)
    notification.processed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"detail": "Confirmed", "id": str(notification_id)}
