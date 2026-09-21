"""XDG-compliant locations for runtime data produced by the shell."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import (
    CLIPBOARD_HISTORY_PATH,
    CLIPBOARD_IMAGES_DIR,
    DESKTOP_ICONS_PATH,
    PINNED_APPS_PATH,
    TASKS_PATH,
)

_APP_DIRECTORY = "waybar-shell"


def xdg_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / _APP_DIRECTORY


def xdg_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / _APP_DIRECTORY


def xdg_runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "jugoo"
    return Path(f"/tmp/jugoo-{os.getuid()}")


def notifications_history_path() -> Path:
    return xdg_data_dir() / "notifications.json"


def pinned_apps_path() -> Path:
    return xdg_data_dir() / PINNED_APPS_PATH


def tasks_path() -> Path:
    return xdg_data_dir() / TASKS_PATH


def briefing_path() -> Path:
    """Last startup briefing message (single entry, next to tasks.json)."""
    return xdg_data_dir() / "briefing.json"


def ai_chat_path() -> Path:
    """Rolling conversation memory for the under-bar AI ask prompt."""
    return xdg_data_dir() / "ai_chat.json"


def reminder_state_path() -> Path:
    """Per-task reminder cooldown / mention bookkeeping (not user task data)."""
    return xdg_data_dir() / "reminder_state.json"


def clipboard_history_path() -> Path:
    return xdg_data_dir() / CLIPBOARD_HISTORY_PATH


def clipboard_images_dir() -> Path:
    """Binary clipboard image store (relative paths live under this tree)."""
    return xdg_data_dir() / CLIPBOARD_IMAGES_DIR


def settings_path() -> Path:
    return xdg_data_dir() / "settings.json"


def wallpaper_dir() -> Path:
    """Installed desktop wallpaper copies (``current.<ext>``)."""
    return xdg_data_dir() / "wallpapers"


def desktop_icons_path() -> Path:
    """User desktop shortcuts (independent of ``pinned-apps.json``)."""
    return xdg_data_dir() / DESKTOP_ICONS_PATH


def user_app_shortcuts_path() -> Path:
    """User-managed application keybinds (JSON source of truth)."""
    return xdg_data_dir() / "user-app-shortcuts.json"


def aidyc_tasks_cache_path() -> Path:
    """Local cache/links for AIDYC tasks (source of truth remains AIDYC)."""
    return xdg_data_dir() / "aidyc_tasks_cache.json"


def notification_icons_dir() -> Path:
    return xdg_cache_dir() / "notification-icons"


def media_artwork_dir() -> Path:
    return xdg_cache_dir() / "media-artwork"


def migrate_legacy_file(destination: Path, legacy_path: Path) -> None:
    """Copy a legacy state file once without overwriting the XDG location."""
    if destination.exists() or not legacy_path.is_file():
        return
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, destination)
    except OSError:
        # Migration is best-effort; normal startup must still work with no state.
        return
