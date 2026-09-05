"""Parse FreeDesktop ``.desktop`` files into the shell application catalog."""

from __future__ import annotations

import configparser
import os
from pathlib import Path

from ...icons import APPLICATION_DIRS, FALLBACK_ICON
from ...models import DesktopApplication, normalize_desktop_id

_TRUE = {"1", "true", "yes", "on"}


def scan_desktop_applications(
    directories: tuple[Path, ...] | None = None,
) -> tuple[DesktopApplication, ...]:
    """Return unique visible applications, first path wins (user overrides system)."""
    apps: dict[str, DesktopApplication] = {}
    for directory in directories if directories is not None else APPLICATION_DIRS:
        if not directory.is_dir():
            continue
        try:
            desktop_files = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for desktop_file in desktop_files:
            application = read_desktop_application(desktop_file)
            if application is None or application.id in apps:
                continue
            apps[application.id] = application
    return tuple(sorted(apps.values(), key=lambda item: item.name.casefold()))


def desktop_directories_stamp(directories: tuple[Path, ...] | None = None) -> tuple[tuple[str, int], ...]:
    """Cheap mtime fingerprint so the launcher can refresh only when files change."""
    stamp: list[tuple[str, int]] = []
    for directory in directories if directories is not None else APPLICATION_DIRS:
        try:
            mtime_ns = directory.stat().st_mtime_ns if directory.is_dir() else 0
        except OSError:
            mtime_ns = 0
        stamp.append((str(directory), int(mtime_ns)))
    return tuple(stamp)


def read_desktop_application(path: Path) -> DesktopApplication | None:
    entry = _read_desktop_entry(path)
    if entry is None:
        return None
    if entry.get("Type", "Application").strip() not in {"", "Application"}:
        return None
    if not _should_show(entry):
        return None

    ident = normalize_desktop_id(path.stem)
    name = entry.get("Name", "").strip() or ident
    icon = _icon_name(entry.get("Icon", "").strip())
    return DesktopApplication(
        id=ident,
        name=name,
        icon=icon or FALLBACK_ICON,
        exec_cmd=_strip_exec_field_codes(entry.get("Exec", "").strip()),
        wm_class=entry.get("StartupWMClass", "").strip(),
        categories=_desktop_list(entry.get("Categories", "")),
        keywords=_desktop_list(entry.get("Keywords", "")),
        comment=entry.get("Comment", "").strip(),
        generic_name=entry.get("GenericName", "").strip(),
        terminal=_is_true(entry.get("Terminal", "")),
        desktop_path=str(path),
    )


def strip_exec_field_codes(command: str) -> str:
    return _strip_exec_field_codes(command)


def _read_desktop_entry(path: Path) -> configparser.SectionProxy | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        return parser["Desktop Entry"]
    except (OSError, KeyError, configparser.Error):
        return None


def _should_show(entry: configparser.SectionProxy) -> bool:
    if _is_true(entry.get("Hidden", "")) or _is_true(entry.get("NoDisplay", "")):
        return False
    current = {
        part.strip()
        for part in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")
        if part.strip()
    }
    only_show = _desktop_list(entry.get("OnlyShowIn", ""))
    if only_show and current and current.isdisjoint(only_show):
        return False
    not_show = _desktop_list(entry.get("NotShowIn", ""))
    if not_show and current and not current.isdisjoint(not_show):
        return False
    return True


def _icon_name(icon: str) -> str:
    if Path(icon).is_absolute():
        return Path(icon).stem
    return icon


def _desktop_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _is_true(value: str) -> bool:
    return value.strip().casefold() in _TRUE


def _strip_exec_field_codes(command: str) -> str:
    tokens: list[str] = []
    for token in command.split():
        if token.startswith("%") and len(token) == 2:
            continue
        tokens.append(token)
    return " ".join(tokens)
