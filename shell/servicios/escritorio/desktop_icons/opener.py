"""Safe open handlers for desktop shortcuts (no free shell for arbitrary text)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from ....actions import UNSUPPORTED, known_action_names
from ....models import normalize_desktop_id
from .model import DesktopShortcut

OpenExecutor = Callable[[Sequence[str]], None]


class DesktopOpenError(RuntimeError):
    """Raised when a shortcut cannot be opened safely."""


_DANGEROUS_BASENAMES = frozenset(
    {
        "rm",
        "sudo",
        "doas",
        "shutdown",
        "reboot",
        "poweroff",
        "halt",
        "mkfs",
        "dd",
        "wipefs",
        "mkswap",
        "fdisk",
        "parted",
        "cryptsetup",
        "passwd",
        "userdel",
        "chmod",
        "chown",
    }
)


def open_shortcut(
    shortcut: DesktopShortcut,
    *,
    launch_application: Callable[[str], None] | None = None,
    dispatch_jugoo_action: Callable[[str], str | None] | None = None,
    executor: OpenExecutor | None = None,
) -> None:
    """Open ``shortcut`` according to its type. Never runs free shell for ``command``."""
    if shortcut.type == "command":
        raise DesktopOpenError(
            "desktop shortcuts of type 'command' are disabled "
            "(no free shell execution)"
        )
    if shortcut.type == "application":
        _open_application(shortcut, launch_application=launch_application)
        return
    if shortcut.type == "file":
        _open_path(shortcut, expect_dir=False, executor=executor)
        return
    if shortcut.type == "directory":
        _open_path(shortcut, expect_dir=True, executor=executor)
        return
    if shortcut.type == "action":
        _open_action(shortcut, dispatch_jugoo_action=dispatch_jugoo_action)
        return
    raise DesktopOpenError(f"unsupported shortcut type {shortcut.type!r}")


def _open_application(
    shortcut: DesktopShortcut,
    *,
    launch_application: Callable[[str], None] | None,
) -> None:
    app_id = normalize_desktop_id(shortcut.target)
    if not app_id:
        raise DesktopOpenError("application target is empty")
    if launch_application is not None:
        launch_application(app_id)
        return
    if shutil.which("gtk-launch"):
        _run(("gtk-launch", app_id), executor=None)
        return
    raise DesktopOpenError(f"cannot launch application {app_id!r}")


def _open_path(
    shortcut: DesktopShortcut,
    *,
    expect_dir: bool,
    executor: OpenExecutor | None,
) -> None:
    path = Path(shortcut.target).expanduser()
    if not path.is_absolute():
        raise DesktopOpenError("file/directory targets must be absolute paths")
    if not path.exists():
        raise DesktopOpenError(f"path does not exist: {path}")
    if expect_dir and not path.is_dir():
        raise DesktopOpenError(f"not a directory: {path}")
    if not expect_dir and path.is_dir():
        raise DesktopOpenError(f"expected a file, got directory: {path}")
    if (
        path.is_file()
        and path.name.casefold() in _DANGEROUS_BASENAMES
        and os.access(path, os.X_OK)
    ):
        raise DesktopOpenError(f"refusing to open potentially dangerous path: {path.name}")
    uri = path.resolve().as_uri()
    if shutil.which("xdg-open"):
        _run(("xdg-open", uri), executor=executor)
        return
    raise DesktopOpenError("xdg-open is not available")


def _open_action(
    shortcut: DesktopShortcut,
    *,
    dispatch_jugoo_action: Callable[[str], str | None] | None,
) -> None:
    name = shortcut.target.strip()
    if name in UNSUPPORTED:
        raise DesktopOpenError(UNSUPPORTED[name])
    if name not in known_action_names():
        raise DesktopOpenError(f"unknown Jugoo action {name!r}")
    if dispatch_jugoo_action is not None:
        error = dispatch_jugoo_action(name)
        if error:
            raise DesktopOpenError(error)
        return
    raise DesktopOpenError("Jugoo action dispatcher is not available")


def _run(command: Sequence[str], *, executor: OpenExecutor | None) -> None:
    if executor is not None:
        executor(command)
        return
    try:
        subprocess.Popen(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise DesktopOpenError(str(error)) from error
