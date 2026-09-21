"""Atomic JSON persistence for desktop icons. Corrupt files never wipe data."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .model import DesktopShortcut, shortcut_from_dict

DESKTOP_ICONS_VERSION = 1


class DesktopIconsStoreError(RuntimeError):
    """Raised when a save fails after validation."""


def load_desktop_icons(path: Path) -> tuple[DesktopShortcut, ...]:
    """Load shortcuts. Missing/corrupt/invalid files return empty without deleting."""
    if not path.is_file():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"shell: desktop-icons: could not read {path}: {error}")
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        print(f"shell: desktop-icons: corrupt JSON in {path}: {error} (keeping file)")
        return ()
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("icons", [])
    else:
        print(f"shell: desktop-icons: unexpected payload in {path} (keeping file)")
        return ()
    if not isinstance(items, list):
        print(f"shell: desktop-icons: icons is not a list in {path} (keeping file)")
        return ()
    shortcuts: list[DesktopShortcut] = []
    seen: set[str] = set()
    for raw in items:
        shortcut = shortcut_from_dict(raw)
        if shortcut is None or shortcut.id in seen:
            continue
        seen.add(shortcut.id)
        shortcuts.append(shortcut)
    return tuple(shortcuts)


def save_desktop_icons(path: Path, shortcuts: tuple[DesktopShortcut, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": DESKTOP_ICONS_VERSION,
        "icons": [item.to_dict() for item in shortcuts],
    }
    encoded = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=".desktop-icons-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise DesktopIconsStoreError(f"could not save {path}: {error}") from error
