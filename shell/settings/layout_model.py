"""Layout model prepared for a future visual module editor."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

Region = Literal["left", "center", "right"]


@dataclass
class ModuleSlot:
    """One bar module placement. Free x/y reserved for a future canvas editor."""

    id: str
    region: Region
    order: int
    visible: bool = True
    x: float | None = None
    y: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModuleSlot:
        return cls(
            id=str(payload["id"]),
            region=str(payload.get("region", "right")),  # type: ignore[arg-type]
            order=int(payload.get("order", 0)),
            visible=bool(payload.get("visible", True)),
            x=payload.get("x"),
            y=payload.get("y"),
        )


DEFAULT_LAYOUT: tuple[ModuleSlot, ...] = (
    ModuleSlot("active_window", "left", 0),
    ModuleSlot("pinned_apps", "left", 1),
    ModuleSlot("workspaces", "center", 0),
    ModuleSlot("tray", "right", 0),
    ModuleSlot("notifications", "right", 1),
    ModuleSlot("ethernet", "right", 2),
    ModuleSlot("stats", "right", 3),
    ModuleSlot("tasks", "right", 4),
    ModuleSlot("clock", "right", 5),
    ModuleSlot("settings", "right", 6),
    ModuleSlot("power", "right", 7),
)


def default_layout_json() -> str:
    return json.dumps([slot.to_dict() for slot in DEFAULT_LAYOUT], ensure_ascii=False)


def parse_layout(raw: str | None) -> tuple[ModuleSlot, ...]:
    if not raw or not raw.strip():
        return DEFAULT_LAYOUT
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_LAYOUT
    if not isinstance(payload, list):
        return DEFAULT_LAYOUT
    slots: list[ModuleSlot] = []
    for item in payload:
        if isinstance(item, dict) and "id" in item:
            try:
                slots.append(ModuleSlot.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
    return tuple(slots) if slots else DEFAULT_LAYOUT


def layout_for_region(slots: tuple[ModuleSlot, ...], region: Region) -> tuple[ModuleSlot, ...]:
    filtered = [slot for slot in slots if slot.region == region and slot.visible]
    filtered.sort(key=lambda slot: slot.order)
    return tuple(filtered)
