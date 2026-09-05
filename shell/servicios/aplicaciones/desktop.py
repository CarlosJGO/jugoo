"""Parse FreeDesktop ``.desktop`` files into the shell application catalog."""

from __future__ import annotations

import configparser
import os
from pathlib import Path

from ...icons import FALLBACK_ICON, application_directories
from ...models import DesktopApplication, normalize_desktop_id

_TRUE = {"1", "true", "yes", "on"}
_NEW_WINDOW_ACTION_IDS = frozenset(
    {
        "new-window",
        "new-instance",
        "window-new",
        "new_window",
        "newwindow",
        "newinstance",
    }
)


def scan_desktop_applications(
    directories: tuple[Path, ...] | None = None,
) -> tuple[DesktopApplication, ...]:
    """Return unique visible applications, first path wins (user overrides system)."""
    apps: dict[str, DesktopApplication] = {}
    for directory in directories if directories is not None else application_directories():
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
    for directory in directories if directories is not None else application_directories():
        try:
            mtime_ns = directory.stat().st_mtime_ns if directory.is_dir() else 0
        except OSError:
            mtime_ns = 0
        stamp.append((str(directory), int(mtime_ns)))
    return tuple(stamp)


def read_desktop_application(path: Path) -> DesktopApplication | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (OSError, KeyError, configparser.Error):
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
        new_instance_exec=_new_instance_exec(parser, entry),
    )


def strip_exec_field_codes(command: str) -> str:
    return _strip_exec_field_codes(command)


def _new_instance_exec(parser: configparser.ConfigParser, entry: configparser.SectionProxy) -> str:
    for action_id in _desktop_list(entry.get("Actions", "")):
        normalized = action_id.casefold().replace("_", "-")
        if normalized not in _NEW_WINDOW_ACTION_IDS:
            continue
        section = f"Desktop Action {action_id}"
        if not parser.has_section(section):
            continue
        exec_cmd = parser[section].get("Exec", "").strip()
        if exec_cmd:
            return _strip_exec_field_codes(exec_cmd)
    return ""


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
        if token.startswith("@@"):
            continue
        tokens.append(token)
    return " ".join(tokens)
