"""Safe verification for PowerService — never executes real power actions."""

from __future__ import annotations

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
        assert service.last_commands[0][:3] == ["noctalia", "msg", "session"]


def verify_mock_executor() -> None:
    executed: list[list[str]] = []

    def mock_executor(command) -> None:
        executed.append(list(command))

    service = PowerService(executor=mock_executor)
    service.reboot()
    assert executed == [["noctalia", "msg", "session", "reboot"]]
    assert service.last_action == ACTION_REBOOT


if __name__ == "__main__":
    verify_power_service_dry_run()
    verify_mock_executor()
    print("power verification OK (no destructive actions executed)")
