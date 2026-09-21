"""Desktop icon shortcuts — isolated from pinned apps and wallpaper."""

from .model import (
    SHORTCUT_TYPES,
    DesktopShortcut,
    ShortcutType,
    new_shortcut_id,
)
from .service import (
    DESKTOP_ICONS_CHANGED,
    DesktopIconsService,
)

__all__ = (
    "DESKTOP_ICONS_CHANGED",
    "SHORTCUT_TYPES",
    "DesktopIconsService",
    "DesktopShortcut",
    "ShortcutType",
    "new_shortcut_id",
)
