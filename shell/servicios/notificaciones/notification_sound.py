"""Non-blocking notification sound playback."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

_PLAYBACK_TIMEOUT_SEC = 5.0
MIN_SOUND_INTERVAL_SEC = 1.5


class NotificationSoundGate:
    """Prevent overlapping notification sounds and enforce a minimum start gap."""

    def __init__(self, minimum_interval: float = MIN_SOUND_INTERVAL_SEC) -> None:
        self._minimum_interval = max(0.0, float(minimum_interval))
        self._lock = threading.Lock()
        self._playing = False
        self._last_started_at = float("-inf")

    def try_start(self, now: float | None = None) -> bool:
        current_time = time.monotonic() if now is None else float(now)
        with self._lock:
            if self._playing or current_time - self._last_started_at < self._minimum_interval:
                return False
            self._playing = True
            self._last_started_at = current_time
            return True

    def finish(self) -> None:
        with self._lock:
            self._playing = False


_SOUND_GATE = NotificationSoundGate()


def _playback_commands(path: Path) -> tuple[list[str], ...]:
    return (
        ["pw-play", "--volume", "0.55", str(path)],
        ["pw-play", str(path)],
        ["paplay", str(path)],
        [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "error",
            "-volume",
            "55",
            "-i",
            str(path),
        ],
    )


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
    if not _SOUND_GATE.try_start():
        return

    def worker() -> None:
        try:
            available = [
                command
                for command in _playback_commands(resolved)
                if shutil.which(command[0])
            ]
            if not available:
                print(
                    "shell: notifications: No supported audio player found "
                    "(tried pw-play, paplay, ffplay)"
                )
                return

            for command in available:
                try:
                    subprocess.run(
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
        finally:
            _SOUND_GATE.finish()

    try:
        threading.Thread(target=worker, name="notification-sound", daemon=True).start()
    except RuntimeError as error:
        _SOUND_GATE.finish()
        print(f"shell: notifications: could not start sound playback: {error}")
