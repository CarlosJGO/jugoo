"""Session and power actions for the shell power menu.

Primary backend: Noctalia session commands (same stack as Hyprland binds).
Fallbacks: loginctl / systemctl when Noctalia is unavailable.

Never call destructive actions from automated tests — use ``dry_run=True`` or
inject a custom ``executor`` that records commands instead of running them.
"""

from __future__ import annotations

import os
import subprocess
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

_NOCTALIA_SESSION_ACTION = {
    ACTION_LOCK: "lock",
    ACTION_SUSPEND: "suspend",
    ACTION_LOGOUT: "logout",
    ACTION_REBOOT: "reboot",
    ACTION_SHUTDOWN: "shutdown",
}


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
    noctalia_action = _NOCTALIA_SESSION_ACTION[action]
    chain: list[tuple[str, ...]] = [
        ("noctalia", "msg", "session", noctalia_action),
    ]

    if action == ACTION_LOCK:
        chain.append(("loginctl", "lock-session"))
    elif action == ACTION_SUSPEND:
        chain.append(("systemctl", "suspend"))
    elif action == ACTION_LOGOUT:
        session_id = os.environ.get("XDG_SESSION_ID")
        if session_id:
            chain.append(("loginctl", "terminate-session", session_id))
    elif action == ACTION_REBOOT:
        chain.append(("systemctl", "reboot"))
    elif action == ACTION_SHUTDOWN:
        chain.append(("systemctl", "poweroff"))

    return tuple(chain)


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
