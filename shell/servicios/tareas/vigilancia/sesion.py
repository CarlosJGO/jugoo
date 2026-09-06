"""Systemd is the only process that may spawn the task watcher."""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path
from typing import Callable

from ....desktop_install import task_watcher_service_path
from ....runtime_paths import xdg_runtime_dir

TASK_WATCHER_UNIT = "jugoo-task-watcher.service"

CommandRunner = Callable[..., subprocess.CompletedProcess]


def watcher_lock_path() -> Path:
    return xdg_runtime_dir() / "task-watcher.lock"


def acquire_watcher_lock(path: Path | None = None) -> int | None:
    """Exclusive lock so a second watcher exits instead of duplicating work."""
    lock_path = path if path is not None else watcher_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(handle)
        return None
    os.write(handle, f"{os.getpid()}\n".encode("ascii"))
    return handle


def release_watcher_lock(handle: int | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(handle)
    except OSError:
        pass


def ensure_task_watcher_service(
    *,
    runner: CommandRunner | None = None,
    unit_path: Path | None = None,
) -> str:
    """Ask systemd to start the existing unit. Never fork ``jugoo --task-watcher``."""
    path = unit_path if unit_path is not None else task_watcher_service_path()
    if not path.is_file():
        print("Task watcher: systemd unit missing")
        return "missing"
    command = ["systemctl", "--user", "enable", "--now", TASK_WATCHER_UNIT]
    run = runner or subprocess.run
    try:
        result = run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"Task watcher: systemd start failed: {error}")
        return "failed"
    if getattr(result, "returncode", 1) != 0:
        stderr = getattr(result, "stderr", "") or ""
        print(f"Task watcher: systemd start failed: {stderr.strip() or 'nonzero exit'}")
        return "failed"
    return "started"


def enable_task_watcher_service(*, runner: CommandRunner | None = None) -> None:
    run = runner or subprocess.run
    try:
        run(
            ["systemctl", "--user", "enable", TASK_WATCHER_UNIT],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
