"""Immutable desktop shortcut records (independent of pinned-apps)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

ShortcutType = Literal["application", "file", "directory", "action", "command"]

SHORTCUT_TYPES: tuple[ShortcutType, ...] = (
    "application",
    "file",
    "directory",
    "action",
    "command",
)

# User-facing create UI excludes free shell commands.
CREATABLE_TYPES: tuple[ShortcutType, ...] = (
    "application",
    "file",
    "directory",
    "action",
)


def new_shortcut_id() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class DesktopShortcut:
    """One desktop icon. ``monitor`` is reserved for future multi-monitor support."""

    id: str
    name: str
    type: ShortcutType
    target: str
    icon: str = ""
    x: int = 48
    y: int = 48
    monitor: str | None = None

    def with_position(self, x: int, y: int) -> DesktopShortcut:
        return replace(self, x=int(x), y=int(y))

    def with_name(self, name: str) -> DesktopShortcut:
        return replace(self, name=str(name).strip() or self.name)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "target": self.target,
            "icon": self.icon,
            "x": int(self.x),
            "y": int(self.y),
        }
        if self.monitor:
            payload["monitor"] = self.monitor
        return payload


def shortcut_from_dict(raw: object) -> DesktopShortcut | None:
    if not isinstance(raw, dict):
        return None
    shortcut_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    kind = str(raw.get("type") or "").strip()
    target = str(raw.get("target") or "").strip()
    if not shortcut_id or not name or kind not in SHORTCUT_TYPES or not target:
        return None
    try:
        x = int(raw.get("x", 48))
        y = int(raw.get("y", 48))
    except (TypeError, ValueError):
        x, y = 48, 48
    icon = str(raw.get("icon") or "").strip()
    monitor_raw = raw.get("monitor")
    monitor = str(monitor_raw).strip() if monitor_raw else None
    return DesktopShortcut(
        id=shortcut_id,
        name=name,
        type=kind,  # type: ignore[arg-type]
        target=target,
        icon=icon,
        x=x,
        y=y,
        monitor=monitor or None,
    )
