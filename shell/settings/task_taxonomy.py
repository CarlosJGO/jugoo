"""Dynamic task categories and priorities (settings-backed, not JSON UI)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..models import TaskSnapshot

MAX_TASK_CATEGORIES = 24
MAX_TASK_PRIORITIES = 16


@dataclass(frozen=True)
class TaskCategory:
    id: str
    label: str
    color: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "color": self.color}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskCategory | None:
        label = str(payload.get("label", "")).strip()
        if not label:
            return None
        ident = str(payload.get("id", "")).strip() or _slug_id(label)
        color = str(payload.get("color", "")).strip()
        return cls(id=ident, label=label, color=color)


@dataclass(frozen=True)
class TaskPriority:
    id: str
    label: str
    weight: int = 0

    def to_dict(self) -> dict[str, str | int]:
        return {"id": self.id, "label": self.label, "weight": int(self.weight)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskPriority | None:
        label = str(payload.get("label", "")).strip()
        if not label:
            return None
        ident = str(payload.get("id", "")).strip() or _slug_id(label)
        try:
            weight = int(payload.get("weight", 0))
        except (TypeError, ValueError):
            weight = 0
        return cls(id=ident, label=label, weight=max(0, min(weight, 99)))


DEFAULT_TASK_CATEGORIES: tuple[TaskCategory, ...] = (
    TaskCategory(id="universidad", label="Universidad", color="#7C8CFF"),
    TaskCategory(id="trabajo", label="Trabajo", color="#65B7FF"),
    TaskCategory(id="relax", label="Relax", color="#5CE6A8"),
    TaskCategory(id="personal", label="Personal", color="#F5C76B"),
)

DEFAULT_TASK_PRIORITIES: tuple[TaskPriority, ...] = (
    TaskPriority(id="baja", label="Baja", weight=0),
    TaskPriority(id="media", label="Media", weight=1),
    TaskPriority(id="alta", label="Alta", weight=3),
)


def default_categories_json() -> str:
    return serialize_categories(DEFAULT_TASK_CATEGORIES)


def default_priorities_json() -> str:
    return serialize_priorities(DEFAULT_TASK_PRIORITIES)


def parse_categories(raw: str | None) -> tuple[TaskCategory, ...]:
    items = _parse_list(raw)
    if items is None:
        return DEFAULT_TASK_CATEGORIES
    categories: list[TaskCategory] = []
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        category = TaskCategory.from_dict(entry)
        if category is None or category.id in seen:
            continue
        seen.add(category.id)
        categories.append(category)
        if len(categories) >= MAX_TASK_CATEGORIES:
            break
    return tuple(categories) if categories else DEFAULT_TASK_CATEGORIES


def parse_priorities(raw: str | None) -> tuple[TaskPriority, ...]:
    items = _parse_list(raw)
    if items is None:
        return DEFAULT_TASK_PRIORITIES
    priorities: list[TaskPriority] = []
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        priority = TaskPriority.from_dict(entry)
        if priority is None or priority.id in seen:
            continue
        seen.add(priority.id)
        priorities.append(priority)
        if len(priorities) >= MAX_TASK_PRIORITIES:
            break
    if not priorities:
        return DEFAULT_TASK_PRIORITIES
    return tuple(priorities)


def serialize_categories(categories: tuple[TaskCategory, ...] | list[TaskCategory]) -> str:
    limited = list(categories)[:MAX_TASK_CATEGORIES]
    return json.dumps([item.to_dict() for item in limited], ensure_ascii=False)


def serialize_priorities(priorities: tuple[TaskPriority, ...] | list[TaskPriority]) -> str:
    limited = list(priorities)[:MAX_TASK_PRIORITIES]
    return json.dumps([item.to_dict() for item in limited], ensure_ascii=False)


def priority_weight_map(priorities: tuple[TaskPriority, ...] | list[TaskPriority]) -> dict[str, int]:
    return {item.id: int(item.weight) for item in priorities}


def category_label_map(categories: tuple[TaskCategory, ...] | list[TaskCategory]) -> dict[str, str]:
    return {item.id: item.label for item in categories}


def priority_label_map(priorities: tuple[TaskPriority, ...] | list[TaskPriority]) -> dict[str, str]:
    return {item.id: item.label for item in priorities}


def new_category_id(label: str) -> str:
    return f"{_slug_id(label)}-{uuid4().hex[:6]}"


def new_priority_id(label: str) -> str:
    return f"{_slug_id(label)}-{uuid4().hex[:6]}"


def sort_by_priority(
    snapshots: list[TaskSnapshot] | tuple[TaskSnapshot, ...],
    weights: dict[str, int],
) -> list[TaskSnapshot]:
    return sorted(
        snapshots,
        key=lambda item: (
            -(weights.get(item.priority_id or "", 0)),
            item.occurrence_date,
            item.title.casefold(),
        ),
    )


def _parse_list(raw: str | None) -> list[Any] | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    return payload


def _slug_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    return slug[:32] or "item"
