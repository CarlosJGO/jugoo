"""Safe verification for PowerService — never executes real power actions."""

from __future__ import annotations

import os

from shell.servicios.energia.power import (
    ACTION_LOCK,
    ACTION_LOGOUT,
    ACTION_REBOOT,
    ACTION_SHUTDOWN,
    ACTION_SUSPEND,
    PowerService,
)


def _recording_executor(commands: list[list[str]]) -> None:
    raise AssertionError(f"executor must not run during dry-run: {commands}")


def verify_power_service_dry_run() -> None:
    service = PowerService(dry_run=True, executor=_recording_executor)

    expected_first = {
        ACTION_LOCK: ["loginctl", "lock-session"],
        ACTION_SUSPEND: ["systemctl", "suspend"],
        ACTION_LOGOUT: None,  # depends on env; checked below
        ACTION_REBOOT: ["systemctl", "reboot"],
        ACTION_SHUTDOWN: ["systemctl", "poweroff"],
    }

    for action, method_name in (
        (ACTION_LOCK, "lock"),
        (ACTION_SUSPEND, "suspend"),
        (ACTION_LOGOUT, "logout"),
        (ACTION_REBOOT, "reboot"),
        (ACTION_SHUTDOWN, "shutdown"),
    ):
        service.last_action = None
        service.last_commands = []
        getattr(service, method_name)()
        assert service.last_action == action, f"expected {action}, got {service.last_action}"
        assert service.last_commands, f"expected command chain for {action}"
        first = service.last_commands[0]
        if action == ACTION_LOGOUT:
            assert first[0] in ("loginctl", "hyprctl"), first
            if first[0] == "loginctl":
                assert first[1] in ("terminate-session", "terminate-user"), first
        else:
            assert first == expected_first[action], first


def verify_mock_executor() -> None:
    executed: list[list[str]] = []

    def mock_executor(command) -> None:
        executed.append(list(command))

    service = PowerService(executor=mock_executor)
    service.reboot()
    assert executed == [["systemctl", "reboot"]]
    assert service.last_action == ACTION_REBOOT


def verify_suspend_locks_before_sleep() -> None:
    executed: list[list[str]] = []

    def mock_executor(command) -> None:
        executed.append(list(command))

    service = PowerService(executor=mock_executor)
    service.suspend()
    assert executed == [
        ["loginctl", "lock-session"],
        ["systemctl", "suspend"],
    ]
    assert service.last_action == ACTION_SUSPEND


def verify_logout_prefers_session_id(monkeypatch_env: dict[str, str] | None = None) -> None:
    previous = {key: os.environ.get(key) for key in ("XDG_SESSION_ID", "USER", "LOGNAME")}
    try:
        os.environ.pop("XDG_SESSION_ID", None)
        os.environ.pop("USER", None)
        os.environ.pop("LOGNAME", None)
        if monkeypatch_env:
            os.environ.update(monkeypatch_env)

        executed: list[list[str]] = []

        def mock_executor(command) -> None:
            executed.append(list(command))

        service = PowerService(executor=mock_executor)
        service.logout()
        assert executed, "expected a logout command"
        assert executed[0][0] in ("loginctl", "hyprctl")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    verify_power_service_dry_run()
    verify_mock_executor()
    verify_suspend_locks_before_sleep()
    verify_logout_prefers_session_id({"XDG_SESSION_ID": "42"})
    verify_logout_prefers_session_id({"USER": "aidyc"})
    verify_logout_prefers_session_id(None)
    print("power verification OK (no destructive actions executed)")
