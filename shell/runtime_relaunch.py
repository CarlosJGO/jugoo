"""Relaunch the Jugoo shell process in-place."""

from __future__ import annotations

import os
import shutil
import sys


def relaunch_argv() -> list[str]:
    """Build argv for replacing this process with a fresh Jugoo instance."""
    jugoo = shutil.which("jugoo")
    if jugoo:
        return [jugoo]
    return [sys.executable, "-m", "shell", *sys.argv[1:]]


def relaunch_shell() -> None:
    """Replace the current process with a new Jugoo shell (never returns)."""
    argv = relaunch_argv()
    os.execv(argv[0], argv)
