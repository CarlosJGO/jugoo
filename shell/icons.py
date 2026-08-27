"""Desktop-entry to icon-theme-name resolution.

The returned values are icon *names*, never glyphs.  GTK resolves them through
the active Breeze/hicolor/XDG icon theme with ``Gtk.Image.new_from_icon_name``.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from .models import Window


FALLBACK_ICON = "application-x-executable"
DESKTOP_ICON = "user-desktop"
KNOWN_APPLICATION_NAMES = {
    "code-oss": "Code - OSS",
    "firefox": "Firefox",
    "kitty": "Kitty",
    "steam": "Steam",
}
APPLICATION_DIRS = (
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
)


@dataclass(frozen=True)
class ApplicationInfo:
    name: str
    icon: str


class DesktopIconResolver:
    """Build a small WM_CLASS index once, then cache all resolution results."""

    def __init__(self, directories: Iterable[Path] = APPLICATION_DIRS) -> None:
        self._directories = tuple(directories)
        self._index: Dict[str, ApplicationInfo] | None = None
        self._cache: Dict[str, ApplicationInfo] = {}

    def describe(self, app_class: str) -> ApplicationInfo:
        key = app_class.casefold().strip()
        if not key:
            return ApplicationInfo("Aplicación", FALLBACK_ICON)
        if key not in self._cache:
            self._cache[key] = self._lookup(key) or ApplicationInfo(
                _friendly_application_name(app_class), FALLBACK_ICON
            )
        return self._cache[key]

    def _lookup(self, app_class: str) -> Optional[ApplicationInfo]:
        index = self._desktop_index()
        direct = index.get(app_class)
        if direct:
            return direct
        # A number of applications expose a qualified WM_CLASS (e.g. org.foo.Bar).
        return next((info for name, info in index.items() if name in app_class or app_class in name), None)

    def _desktop_index(self) -> Dict[str, ApplicationInfo]:
        if self._index is not None:
            return self._index

        index: Dict[str, ApplicationInfo] = {}
        for directory in self._directories:
            if not directory.is_dir():
                continue
            for desktop_file in directory.glob("*.desktop"):
                entry = self._read_desktop_entry(desktop_file)
                if entry is None:
                    continue
                icon = entry.get("Icon", "").strip()
                if Path(icon).is_absolute():
                    # Most absolute desktop-entry icons are hicolor files.  Reduce
                    # them to their theme name so GTK can still resolve them via
                    # new_from_icon_name(), rather than loading an arbitrary file.
                    icon = Path(icon).stem
                if not icon:
                    continue
                application = ApplicationInfo(
                    name=KNOWN_APPLICATION_NAMES.get(
                        desktop_file.stem.casefold(),
                        entry.get("Name", "").strip() or _friendly_application_name(desktop_file.stem),
                    ),
                    icon=icon,
                )
                names = (desktop_file.stem, entry.get("StartupWMClass", ""), application.name)
                for name in names:
                    normalized = name.casefold().strip()
                    if normalized:
                        index.setdefault(normalized, application)

        self._index = index
        return index

    @staticmethod
    def _read_desktop_entry(path: Path) -> configparser.SectionProxy | None:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(path, encoding="utf-8")
            return parser["Desktop Entry"]
        except (OSError, KeyError, configparser.Error):
            return None


_resolver = DesktopIconResolver()


def icon_for_window(window: Window) -> str:
    """Return the system-theme icon name for a Hyprland client."""
    return application_for_window(window).icon


def application_for_window(window: Window) -> ApplicationInfo:
    """Return the desktop-entry identity shared by every shell widget."""
    return _resolver.describe(window.app_class)


def find_desktop_icon(app: str) -> str:
    """Compatibility entry point retained for external consumers of the old module."""
    return _resolver.describe(app).icon


def _friendly_application_name(app_class: str) -> str:
    normalized = app_class.casefold().strip()
    if normalized in KNOWN_APPLICATION_NAMES:
        return KNOWN_APPLICATION_NAMES[normalized]
    return " ".join(part.capitalize() for part in app_class.replace("_", " ").replace("-", " ").split())
