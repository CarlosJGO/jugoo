"""Non-blocking notification sound playback."""

from __future__ import annotations

import shutil
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
        players = (
            ["pw-play", "--volume", "0.55", str(resolved)],
            ["pw-play", str(resolved)],
            ["paplay", str(resolved)],
        )
        available = [cmd for cmd in players if shutil.which(cmd[0])]
        if not available:
            print(
                "shell: notifications: No supported audio player found "
                "(tried pw-play, paplay)"
            )
            return

        for command in available:
            try:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=_PLAYBACK_TIMEOUT_SEC,
                )
                return
            except FileNotFoundError:
                continue
            except subprocess.CalledProcessError as exc:
                print(
                    "shell: notifications: sound playback failed with "
                    f"{command[0]}: rc={exc.returncode} "
                    f"stderr={exc.stderr.decode('utf-8', errors='replace')!r}"
                )
                continue
            except subprocess.TimeoutExpired:
                print(
                    "shell: notifications: sound playback timed out with "
                    f"{command[0]}"
                )
                continue
            except OSError as exc:
                print(
                    "shell: notifications: sound playback OS error with "
                    f"{command[0]}: {exc}"
                )
                continue

        print("shell: notifications: all sound playback attempts failed")

    threading.Thread(target=worker, name="notification-sound", daemon=True).start()
