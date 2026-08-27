"""Non-blocking notification sound playback."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

_PLAYBACK_TIMEOUT_SEC = 1.5


def play_notification_sound(path: Path, *, enabled: bool) -> None:
    if not enabled:
        return
    resolved = path.expanduser()
    if not resolved.is_file():
        print(
            "shell: notifications: Notification sound file not found: "
            f"{resolved}",
        )
        return

    def worker() -> None:
        for command in (
            ["pw-play", "--volume", "0.55", str(resolved)],
            ["pw-play", str(resolved)],
            ["paplay", str(resolved)],
        ):
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=_PLAYBACK_TIMEOUT_SEC,
                )
                return
            except (OSError, subprocess.SubprocessError):
                continue

    threading.Thread(target=worker, name="notification-sound", daemon=True).start()
