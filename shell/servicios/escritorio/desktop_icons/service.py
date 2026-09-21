"""Owns desktop shortcut CRUD, persistence, and open requests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ....eventbus import EventBus
from ....models import normalize_desktop_id
from ....runtime_paths import desktop_icons_path
from .model import (
    CREATABLE_TYPES,
    DesktopShortcut,
    ShortcutType,
    new_shortcut_id,
)
from .opener import DesktopOpenError, open_shortcut
from .store import load_desktop_icons, save_desktop_icons

DESKTOP_ICONS_CHANGED = "desktop_icons_changed"

LaunchApplication = Callable[[str], None]
DispatchAction = Callable[[str], str | None]


class DesktopIconsService:
    """Single source of truth for desktop icons (separate from pinned-apps)."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        path: Path | None = None,
        launch_application: LaunchApplication | None = None,
        dispatch_action: DispatchAction | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._path = path if path is not None else desktop_icons_path()
        self._launch_application = launch_application
        self._dispatch_action = dispatch_action
        self._shortcuts: tuple[DesktopShortcut, ...] = ()
        self._selected_id: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def shortcuts(self) -> tuple[DesktopShortcut, ...]:
        return self._shortcuts

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    def start(self) -> None:
        self._shortcuts = load_desktop_icons(self._path)
        self._emit()

    def close(self) -> None:
        return

    def set_launch_application(self, callback: LaunchApplication | None) -> None:
        self._launch_application = callback

    def set_dispatch_action(self, callback: DispatchAction | None) -> None:
        self._dispatch_action = callback

    def create(
        self,
        *,
        name: str,
        type: ShortcutType,
        target: str,
        icon: str = "",
        x: int = 48,
        y: int = 48,
        monitor: str | None = None,
    ) -> DesktopShortcut:
        kind = type
        if kind not in CREATABLE_TYPES:
            raise ValueError(
                f"shortcut type {kind!r} cannot be created from the UI "
                "(command type is intentionally blocked)"
            )
        cleaned_name = str(name).strip()
        cleaned_target = str(target).strip()
        if not cleaned_name:
            raise ValueError("name is required")
        if not cleaned_target:
            raise ValueError("target is required")
        if kind == "application":
            cleaned_target = normalize_desktop_id(cleaned_target)
            if not cleaned_target:
                raise ValueError("application target is empty")
        elif kind in {"file", "directory"}:
            path = Path(cleaned_target).expanduser()
            if not path.is_absolute():
                raise ValueError("file/directory targets must be absolute paths")
            cleaned_target = str(path)
        elif kind == "action":
            from ....actions import UNSUPPORTED, known_action_names

            if cleaned_target in UNSUPPORTED or cleaned_target not in known_action_names():
                raise ValueError(f"unknown Jugoo action {cleaned_target!r}")
        shortcut = DesktopShortcut(
            id=new_shortcut_id(),
            name=cleaned_name,
            type=kind,
            target=cleaned_target,
            icon=str(icon or "").strip(),
            x=int(x),
            y=int(y),
            monitor=monitor,
        )
        self._shortcuts = self._shortcuts + (shortcut,)
        self._persist()
        self._emit()
        return shortcut

    def remove(self, shortcut_id: str) -> bool:
        ident = str(shortcut_id).strip()
        if not ident:
            return False
        remaining = tuple(item for item in self._shortcuts if item.id != ident)
        if len(remaining) == len(self._shortcuts):
            return False
        self._shortcuts = remaining
        if self._selected_id == ident:
            self._selected_id = None
        self._persist()
        self._emit()
        return True

    def move(self, shortcut_id: str, x: int, y: int) -> DesktopShortcut | None:
        """Update position and persist (call on drag end)."""
        updated: list[DesktopShortcut] = []
        moved: DesktopShortcut | None = None
        for item in self._shortcuts:
            if item.id == shortcut_id:
                moved = item.with_position(max(0, int(x)), max(0, int(y)))
                updated.append(moved)
            else:
                updated.append(item)
        if moved is None:
            return None
        self._shortcuts = tuple(updated)
        self._persist()
        self._emit()
        return moved

    def select(self, shortcut_id: str | None) -> None:
        ident = str(shortcut_id).strip() if shortcut_id else None
        if ident and not any(item.id == ident for item in self._shortcuts):
            ident = None
        if ident == self._selected_id:
            return
        self._selected_id = ident
        self._emit()

    def open(self, shortcut_id: str) -> None:
        shortcut = self._by_id(shortcut_id)
        if shortcut is None:
            raise DesktopOpenError(f"unknown shortcut {shortcut_id!r}")
        open_shortcut(
            shortcut,
            launch_application=self._launch_application,
            dispatch_jugoo_action=self._dispatch_action,
        )

    def _by_id(self, shortcut_id: str) -> DesktopShortcut | None:
        for item in self._shortcuts:
            if item.id == shortcut_id:
                return item
        return None

    def _persist(self) -> None:
        try:
            save_desktop_icons(self._path, self._shortcuts)
        except Exception as error:  # noqa: BLE001 — never crash the shell on save
            print(f"shell: desktop-icons: save failed: {error}")

    def _emit(self) -> None:
        self._event_bus.emit(
            DESKTOP_ICONS_CHANGED,
            {
                "shortcuts": self._shortcuts,
                "selected_id": self._selected_id,
            },
        )
