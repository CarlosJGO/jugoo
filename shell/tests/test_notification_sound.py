from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shell.servicios.notificaciones import notification_sound


def test_sound_playback_falls_back_to_ffplay_and_has_finish_margin() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "notification.ogg"
        path.write_bytes(b"test audio")
        attempted: list[list[str]] = []

        def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
            attempted.append(command)
            if command[0] != "ffplay":
                raise subprocess.CalledProcessError(
                    1,
                    command,
                    stderr=b"unsupported audio format",
                )
            assert kwargs["timeout"] == notification_sound._PLAYBACK_TIMEOUT_SEC
            return subprocess.CompletedProcess(command, 0)

        class InlineThread:
            def __init__(self, *, target, **_kwargs) -> None:
                self._target = target

            def start(self) -> None:
                self._target()

        with (
            patch.object(notification_sound.shutil, "which", return_value="/usr/bin/player"),
            patch.object(notification_sound.subprocess, "run", side_effect=run),
            patch.object(notification_sound.threading, "Thread", InlineThread),
        ):
            notification_sound.play_notification_sound(path, enabled=True)

    assert [command[0] for command in attempted] == [
        "pw-play",
        "pw-play",
        "paplay",
        "ffplay",
    ]
    assert notification_sound._PLAYBACK_TIMEOUT_SEC >= 5.0


def test_sound_gate_prevents_overlap_and_enforces_minimum_interval() -> None:
    gate = notification_sound.NotificationSoundGate()

    assert gate.try_start(now=10.0) is True
    assert gate.try_start(now=11.5) is False
    gate.finish()
    assert gate.try_start(now=11.49) is False
    assert gate.try_start(now=11.5) is True
    gate.finish()


def test_incoming_notification_requests_sound_playback() -> None:
    from shell import config as shell_config
    from shell.models import NOTIFICATION_KIND_NORMAL
    from shell.widgets.barra.notifications import NotificationsWidget

    sound_path = Path("/tmp/jugoo-test-notification.ogg")
    snapshot = SimpleNamespace(kind=NOTIFICATION_KIND_NORMAL, id=1)
    service = SimpleNamespace(
        paused=False,
        should_play_sound=lambda _snapshot: True,
    )
    widget = SimpleNamespace(
        _sync_badge=lambda: None,
        _service=service,
        _animate_bell=lambda: None,
        _sound_path=sound_path,
    )

    with (
        patch.object(shell_config, "NOTIFICATIONS_SOUND_ENABLED", True),
        patch.object(shell_config, "NOTIFICATIONS_TOAST_ENABLED", False),
        patch("shell.widgets.barra.notifications.play_notification_sound") as play,
    ):
        result = NotificationsWidget._handle_notification_received(widget, snapshot)

    assert result is False
    play.assert_called_once_with(sound_path, enabled=True)


def test_global_sound_setting_change_refreshes_panel_notice() -> None:
    from shell.widgets.barra.notifications import NotificationsWidget

    refreshed: list[bool] = []
    popup = SimpleNamespace(refresh_sound_status=lambda: refreshed.append(True))
    widget = SimpleNamespace(_popup=SimpleNamespace(maybe=popup))
    widget._handle_notification_sound_setting_changed = lambda: (
        NotificationsWidget._handle_notification_sound_setting_changed(widget)
    )

    with patch(
        "shell.widgets.barra.notifications.GLib.idle_add",
        side_effect=lambda callback: callback(),
    ):
        NotificationsWidget._on_settings_changed(
            widget,
            {"key": "notificaciones.sound_enabled"},
        )

    assert refreshed == [True]


def _run() -> None:
    import inspect

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        assert not inspect.signature(test).parameters
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()