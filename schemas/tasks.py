from datetime import date, datetime
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
REPEAT_PATTERN = "^(none|daily|weekly|monthly|yearly)$"


def _clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _clean_weekdays(value):
    if value is None:
        return []
    days = sorted({int(d) for d in value})
    if any(d < 1 or d > 7 for d in days):
        raise ValueError("weekday must be 1..7")
    return days


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#4f46e5", pattern=COLOR_PATTERN)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("name is empty")
        return v


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    color: Optional[str] = Field(default=None, pattern=COLOR_PATTERN)
    sort_order: Optional[int] = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name is empty")
        return v


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    sort_order: int


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=5000)
    project_id: Optional[uuid.UUID] = None
    due_date: Optional[date] = None
    repeat: str = Field(default="none", pattern=REPEAT_PATTERN)
    repeat_interval: int = Field(default=1, ge=1, le=365)
    repeat_weekdays: List[int] = Field(default_factory=list)
    repeat_until: Optional[date] = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("title is empty")
        return v

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, v):
        return _clean_text(v)

    @field_validator("repeat_weekdays", mode="before")
    @classmethod
    def weekdays(cls, v):
        return _clean_weekdays(v)

    @model_validator(mode="after")
    def check_repeat(self):
        if self.repeat != "none" and self.due_date is None:
            raise ValueError("Для повторяющейся задачи нужна дата начала")
        if self.repeat_until and self.due_date and self.repeat_until < self.due_date:
            raise ValueError("Дата окончания повтора раньше даты начала")
        if self.repeat != "weekly":
            self.repeat_weekdays = []
        if self.repeat == "none":
            self.repeat_interval = 1
            self.repeat_until = None
        return self


class TaskUpdate(BaseModel):
    """Partial update. Fields not sent stay unchanged; validated again together with the stored task."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=5000)
    project_id: Optional[uuid.UUID] = None
    due_date: Optional[date] = None
    repeat: Optional[str] = Field(default=None, pattern=REPEAT_PATTERN)
    repeat_interval: Optional[int] = Field(default=None, ge=1, le=365)
    repeat_weekdays: Optional[List[int]] = None
    repeat_until: Optional[date] = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("title is empty")
        return v

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, v):
        return _clean_text(v)

    @field_validator("repeat_weekdays", mode="before")
    @classmethod
    def weekdays(cls, v):
        return None if v is None else _clean_weekdays(v)


class TaskDone(BaseModel):
    done: bool = True
    occurrence_date: Optional[date] = None  # required for recurring tasks


class TaskMove(BaseModel):
    occurrence_date: Optional[date] = None  # required for recurring tasks
    to_date: Optional[date] = None          # None = "без срока" (one-off tasks only)


class TaskSkip(BaseModel):
    occurrence_date: date


class TaskItem(BaseModel):
    task_id: uuid.UUID
    title: str
    notes: Optional[str]
    project_id: Optional[uuid.UUID]
    repeat: str
    repeat_interval: int
    repeat_weekdays: List[int]
    repeat_until: Optional[date]
    series_start: Optional[date]
    source: str
    date: Optional[date]
    occurrence_date: Optional[date]
    is_done: bool
    done_at: Optional[datetime]


class TaskFeed(BaseModel):
    items: List[TaskItem]
    undated: List[TaskItem]
    overdue: List[TaskItem]
