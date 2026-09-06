"""Stable Linux identity for Jugoo. No GTK imports so installers can use it headless."""

from __future__ import annotations

from pathlib import Path

APPLICATION_ID = "com.jugoo.Shell"
APPLICATION_NAME = "Jugoo"
APPLICATION_COMMENT = "Desktop shell para Hyprland"
ICON_NAME = APPLICATION_ID
COMMAND_NAME = "jugoo"

# Hyprland `class` / Wayland app_id. Must stay identical to APPLICATION_ID.
WAYLAND_APP_ID = APPLICATION_ID

TITLE_BAR = APPLICATION_NAME
TITLE_POWER_MENU = f"{APPLICATION_NAME} Power Menu"
TITLE_POWER_CONFIRM = f"{APPLICATION_NAME} Power Confirm"
TITLE_WORKSPACE_PANEL = f"{APPLICATION_NAME} Workspace Panel"
TITLE_WORKSPACE_AUDIO = f"{APPLICATION_NAME} Workspace Audio"
TITLE_NOTIFICATIONS = f"{APPLICATION_NAME} Notifications"
TITLE_NOTIFICATION_GROUP = f"{APPLICATION_NAME} Notification Group"
TITLE_NOTIFICATION_TOAST = f"{APPLICATION_NAME} Notification Toast"
TITLE_CONTROL_CENTER = f"{APPLICATION_NAME} Control Center"
TITLE_NETWORK_PANEL = f"{APPLICATION_NAME} Network Panel"
TITLE_MEDIA_POPUP = f"{APPLICATION_NAME} Media Popup"
TITLE_VOLUME_OSD = f"{APPLICATION_NAME} Volume OSD"
TITLE_CLOCK_CALENDAR = f"{APPLICATION_NAME} Clock Calendar"
TITLE_TASKS = f"{APPLICATION_NAME} Tasks"
TITLE_MEMORY_POPUP = f"{APPLICATION_NAME} Memory Popup"
TITLE_APP_LAUNCHER = f"{APPLICATION_NAME} Launcher"
TITLE_CLIPBOARD_PICKER = f"{APPLICATION_NAME} Clipboard"
TITLE_EMOJI_PICKER = f"{APPLICATION_NAME} Emoji"
TITLE_PINNED_OVERFLOW = f"{APPLICATION_NAME} Pinned Overflow"

_LOGO_CANDIDATES = (
    f"{APPLICATION_ID}.svg",
    f"{APPLICATION_ID}.png",
    "jugoo.svg",
    "jugoo.png",
    "logo.svg",
    "logo.png",
    "icon.svg",
    "icon.png",
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def discover_logo(directory: Path | None = None) -> Path | None:
    """Return the first official logo asset, ignoring placeholder files."""
    root = directory if directory is not None else assets_dir()
    if not root.is_dir():
        return None
    for name in _LOGO_CANDIDATES:
        path = root / name
        if not path.is_file():
            continue
        if "placeholder" in path.stem.casefold():
            continue
        return path
    return None
