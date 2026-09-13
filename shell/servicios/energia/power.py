"""Session and power actions for the shell power menu.

Primary backends match the Hyprland binds stack:
``loginctl`` for lock/logout and ``systemctl`` for suspend/reboot/poweroff.

Never call destructive actions from automated tests — use ``dry_run=True`` or
inject a custom ``executor`` that records commands instead of running them.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, Sequence

PowerExecutor = Callable[[Sequence[str]], None]

ACTION_LOCK = "lock"
ACTION_SUSPEND = "suspend"
ACTION_LOGOUT = "logout"
ACTION_REBOOT = "reboot"
ACTION_SHUTDOWN = "shutdown"

POWER_ACTIONS = (
    ACTION_LOCK,
    ACTION_SUSPEND,
    ACTION_LOGOUT,
    ACTION_REBOOT,
    ACTION_SHUTDOWN,
)

# Brief pause so the locker can paint before the machine sleeps.
_LOCK_BEFORE_SUSPEND_DELAY_S = 0.5


class PowerError(RuntimeError):
    """Raised when a power action cannot be dispatched."""


class PowerService:
    """Dispatches lock, suspend, logout, reboot, and shutdown."""

    def __init__(
        self,
        *,
        executor: PowerExecutor | None = None,
        dry_run: bool = False,
    ) -> None:
        self._executor = executor or _default_executor
        self._dry_run = dry_run
        self.last_action: str | None = None
        self.last_commands: list[list[str]] = []

    def lock(self) -> None:
        self._dispatch(ACTION_LOCK)

    def suspend(self) -> None:
        """Lock first, then suspend, so wake returns to the lock screen."""
        if self._dry_run:
            self.last_action = ACTION_LOCK
            self.last_commands = [["loginctl", "lock-session"]]
        else:
            try:
                self._executor(("loginctl", "lock-session"))
            except PowerError:
                pass
            time.sleep(_LOCK_BEFORE_SUSPEND_DELAY_S)
        self._dispatch(ACTION_SUSPEND)

    def logout(self) -> None:
        self._dispatch(ACTION_LOGOUT)

    def reboot(self) -> None:
        self._dispatch(ACTION_REBOOT)

    def shutdown(self) -> None:
        self._dispatch(ACTION_SHUTDOWN)

    def _dispatch(self, action: str) -> None:
        commands = _command_chain(action)
        if self._dry_run:
            self.last_action = action
            self.last_commands = [list(command) for command in commands]
            return

        errors: list[str] = []
        for command in commands:
            try:
                self._executor(command)
                self.last_action = action
                self.last_commands = [list(command)]
                return
            except PowerError as error:
                errors.append(str(error))

        detail = "; ".join(errors) if errors else "no command available"
        raise PowerError(f"could not run power action {action}: {detail}")


def _command_chain(action: str) -> tuple[tuple[str, ...], ...]:
    if action == ACTION_LOCK:
        return (("loginctl", "lock-session"),)
    if action == ACTION_SUSPEND:
        return (("systemctl", "suspend"),)
    if action == ACTION_LOGOUT:
        session_id = os.environ.get("XDG_SESSION_ID")
        if session_id:
            return (("loginctl", "terminate-session", session_id),)
        user = os.environ.get("USER") or os.environ.get("LOGNAME")
        if user:
            return (("loginctl", "terminate-user", user),)
        return (("hyprctl", "dispatch", "exit"),)
    if action == ACTION_REBOOT:
        return (("systemctl", "reboot"),)
    if action == ACTION_SHUTDOWN:
        return (("systemctl", "poweroff"),)
    raise PowerError(f"unknown power action: {action}")


def _default_executor(command: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PowerError(str(error)) from error

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        detail = stderr or f"exit code {completed.returncode}"
        raise PowerError(detail)
