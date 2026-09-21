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

    expected = {
        ACTION_LOCK: [["loginctl", "lock-session"]],
        ACTION_SUSPEND: [
            ["loginctl", "lock-session"],
            ["systemctl", "suspend"],
        ],
        ACTION_REBOOT: [["systemctl", "reboot"]],
        ACTION_SHUTDOWN: [["systemctl", "poweroff"]],
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
        if action == ACTION_LOGOUT:
            first = service.last_commands[0]
            assert first[0] in ("loginctl", "hyprctl"), first
            if first[0] == "loginctl":
                assert first[1] in ("terminate-session", "terminate-user"), first
        else:
            assert service.last_commands == expected[action], service.last_commands


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

    service = PowerService(executor=mock_executor, wait_for_lock=False)
    service.suspend()
    assert executed == [
        ["loginctl", "lock-session"],
        ["systemctl", "suspend"],
    ]
    assert service.last_action == ACTION_SUSPEND
    assert service.last_commands == executed


def verify_fire_and_forget_detection() -> None:
    from shell.servicios.energia.power import _is_fire_and_forget

    assert _is_fire_and_forget(["systemctl", "suspend"])
    assert _is_fire_and_forget(["systemctl", "poweroff"])
    assert _is_fire_and_forget(["systemctl", "reboot"])
    assert not _is_fire_and_forget(["loginctl", "lock-session"])
    assert not _is_fire_and_forget(["systemctl", "status"])


def verify_missing_locker_skips_wait() -> None:
    """If hyprlock is not installed, suspend must not stall waiting for it."""
    import shell.servicios.energia.power as power_mod

    executed: list[list[str]] = []

    def mock_executor(command) -> None:
        executed.append(list(command))

    original_which = power_mod.shutil.which

    def fake_which(name: str):
        if name == "hyprlock":
            return None
        return original_which(name)

    power_mod.shutil.which = fake_which  # type: ignore[method-assign]
    try:
        service = power_mod.PowerService(executor=mock_executor, wait_for_lock=True)
        service.suspend()
    finally:
        power_mod.shutil.which = original_which  # type: ignore[method-assign]

    assert executed == [
        ["loginctl", "lock-session"],
        ["systemctl", "suspend"],
    ]


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


def verify_lock_spawn_uses_random_bg_wrapper() -> None:
    import shell.servicios.energia.power as power_mod

    captured: list[list[str]] = []
    wrapper = "/tmp/fake-xdg-bin/hyprlock-random-bg"

    original_which = power_mod.shutil.which
    original_popen = power_mod.subprocess.Popen

    def fake_which(name: str):
        if name == "hyprlock-random-bg":
            return wrapper
        if name == "hyprlock":
            return "/usr/bin/hyprlock"
        return None

    def fake_popen(argv, **kwargs):
        captured.append(list(argv))
        return object()

    power_mod.shutil.which = fake_which  # type: ignore[method-assign]
    power_mod.subprocess.Popen = fake_popen  # type: ignore[method-assign]
    try:
        power_mod._spawn_hyprlock()
    finally:
        power_mod.shutil.which = original_which  # type: ignore[method-assign]
        power_mod.subprocess.Popen = original_popen  # type: ignore[method-assign]

    assert captured == [[wrapper]], captured


def verify_lock_spawn_falls_back_to_xdg_bin_home() -> None:
    import shell.servicios.energia.power as power_mod

    captured: list[list[str]] = []
    previous_bin = os.environ.get("XDG_BIN_HOME")
    bin_home = "/tmp/fake-xdg-bin-home"
    wrapper = f"{bin_home}/hyprlock-random-bg"

    original_which = power_mod.shutil.which
    original_exists = power_mod.os.path.isfile
    original_access = power_mod.os.access
    original_popen = power_mod.subprocess.Popen

    def fake_which(name: str):
        if name == "hyprlock":
            return "/usr/bin/hyprlock"
        return None

    def fake_isfile(path: str) -> bool:
        return path == wrapper

    def fake_access(path: str, mode: int) -> bool:
        return path == wrapper

    def fake_popen(argv, **kwargs):
        captured.append(list(argv))
        return object()

    os.environ["XDG_BIN_HOME"] = bin_home
    power_mod.shutil.which = fake_which  # type: ignore[method-assign]
    power_mod.os.path.isfile = fake_isfile  # type: ignore[method-assign]
    power_mod.os.access = fake_access  # type: ignore[method-assign]
    power_mod.subprocess.Popen = fake_popen  # type: ignore[method-assign]
    try:
        power_mod._spawn_hyprlock()
    finally:
        if previous_bin is None:
            os.environ.pop("XDG_BIN_HOME", None)
        else:
            os.environ["XDG_BIN_HOME"] = previous_bin
        power_mod.shutil.which = original_which  # type: ignore[method-assign]
        power_mod.os.path.isfile = original_exists  # type: ignore[method-assign]
        power_mod.os.access = original_access  # type: ignore[method-assign]
        power_mod.subprocess.Popen = original_popen  # type: ignore[method-assign]

    assert captured == [[wrapper]], captured


if __name__ == "__main__":
    verify_power_service_dry_run()
    verify_mock_executor()
    verify_suspend_locks_before_sleep()
    verify_fire_and_forget_detection()
    verify_missing_locker_skips_wait()
    verify_lock_spawn_uses_random_bg_wrapper()
    verify_lock_spawn_falls_back_to_xdg_bin_home()
    verify_logout_prefers_session_id({"XDG_SESSION_ID": "42"})
    verify_logout_prefers_session_id({"USER": "aidyc"})
    verify_logout_prefers_session_id(None)
    print("power verification OK (no destructive actions executed)")
