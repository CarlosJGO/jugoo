"""XDG-compliant locations for runtime data produced by the shell."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import PINNED_APPS_PATH

_APP_DIRECTORY = "waybar-shell"


def xdg_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / _APP_DIRECTORY


def xdg_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / _APP_DIRECTORY


def notifications_history_path() -> Path:
    return xdg_data_dir() / "notifications.json"


def pinned_apps_path() -> Path:
    return xdg_data_dir() / PINNED_APPS_PATH


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
