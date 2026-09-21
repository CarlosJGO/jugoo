"""Session and power actions for the shell power menu.

Primary backends match the Hyprland binds stack:
``loginctl`` for lock/logout and ``systemctl`` for suspend/reboot/poweroff.

Never call destructive actions from automated tests — use ``dry_run=True`` or
inject a custom ``executor`` that records commands instead of running them.
"""

from __future__ import annotations

import os
import shutil
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

# Wait for hyprlock (via hypridle's lock_cmd) before requesting sleep.
_LOCK_WAIT_TIMEOUT_S = 2.0
_LOCK_PAINT_SLACK_S = 0.15
_LOCKER_BIN = "hyprlock"
_LOCKER_WRAPPER_NAME = "hyprlock-random-bg"


class PowerError(RuntimeError):
    """Raised when a power action cannot be dispatched."""


class PowerService:
    """Dispatches lock, suspend, logout, reboot, and shutdown."""

    def __init__(
        self,
        *,
        executor: PowerExecutor | None = None,
        dry_run: bool = False,
        wait_for_lock: bool | None = None,
    ) -> None:
        self._uses_default_executor = executor is None
        self._executor = executor or _default_executor
        self._dry_run = dry_run
        # Real runs wait for hyprlock; injected executors (tests) skip by default.
        self._wait_for_lock = (
            self._uses_default_executor if wait_for_lock is None else wait_for_lock
        )
        self.last_action: str | None = None
        self.last_commands: list[list[str]] = []

    def lock(self) -> None:
        self._dispatch(ACTION_LOCK)

    def suspend(self) -> None:
        """Lock first, then suspend, so wake returns to the lock screen."""
        if self._dry_run:
            self.last_action = ACTION_SUSPEND
            self.last_commands = [
                ["loginctl", "lock-session"],
                ["systemctl", "suspend"],
            ]
            return

        lock_cmd = ["loginctl", "lock-session"]
        try:
            self._executor(tuple(lock_cmd))
            self.last_commands = [list(lock_cmd)]
        except PowerError:
            # hypridle still locks on before_sleep_cmd; keep going.
            self.last_commands = []

        if self._wait_for_lock and _locker_available():
            _wait_for_lock_screen()
            if not _hyprlock_is_running():
                _spawn_hyprlock()
                _wait_for_lock_screen(timeout_s=1.0)

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
                command_list = list(command)
                if action == ACTION_SUSPEND and self.last_commands:
                    self.last_commands = [*self.last_commands, command_list]
                else:
                    self.last_commands = [command_list]
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


def _is_fire_and_forget(command: Sequence[str]) -> bool:
    """systemctl sleep/power transitions must not be waited on with a short timeout.

    ``systemctl suspend`` blocks until wake; killing it after 3s races hypridle's
    before_sleep lock and can abort or delay the transition.
    """
    if len(command) < 2 or command[0] != "systemctl":
        return False
    return command[1] in ("suspend", "reboot", "poweroff", "halt", "hibernate")


def _locker_available() -> bool:
    return shutil.which(_LOCKER_BIN) is not None


def _hyprlock_is_running() -> bool:
    if not _locker_available():
        return False
    try:
        completed = subprocess.run(
            ["pidof", _LOCKER_BIN],
            check=False,
            capture_output=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _wait_for_lock_screen(timeout_s: float = _LOCK_WAIT_TIMEOUT_S) -> None:
    """Give hypridle time to spawn hyprlock before the sleep transition."""
    if not _locker_available():
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _hyprlock_is_running():
            time.sleep(_LOCK_PAINT_SLACK_S)
            return
        time.sleep(0.05)


def _locker_wrapper_path() -> str | None:
    """Resolve hyprlock-random-bg via PATH or XDG_BIN_HOME / ~/.local/bin."""
    found = shutil.which(_LOCKER_WRAPPER_NAME)
    if found:
        return found
    bin_home = os.environ.get("XDG_BIN_HOME")
    candidates = []
    if bin_home:
        candidates.append(os.path.join(bin_home, _LOCKER_WRAPPER_NAME))
    home = os.environ.get("HOME") or os.path.expanduser("~")
    if home:
        candidates.append(os.path.join(home, ".local", "bin", _LOCKER_WRAPPER_NAME))
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _spawn_hyprlock() -> None:
    """Fallback when loginctl did not bring hyprlock up in time."""
    binary = _locker_wrapper_path() or shutil.which(_LOCKER_BIN)
    if binary is None:
        return
    try:
        subprocess.Popen(
            [binary],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _default_executor(command: Sequence[str]) -> None:
    argv = list(command)
    if _is_fire_and_forget(argv):
        try:
            subprocess.Popen(
                argv,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise PowerError(str(error)) from error
        return

    try:
        completed = subprocess.run(
            argv,
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
