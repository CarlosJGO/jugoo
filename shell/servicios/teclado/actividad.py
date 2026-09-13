"""Listen for global key presses via Linux evdev without extra packages.

Opens ``/dev/input`` keyboard nodes in a background thread and emits
``KEYBOARD_ACTIVITY`` on the EventBus. Requires read access to those nodes
(typically membership in the ``input`` group).
"""

from __future__ import annotations

from pathlib import Path
import fcntl
import os
import select
import struct
import threading
import time
from typing import Iterable

from ...eventbus import EventBus

KEYBOARD_ACTIVITY = "keyboard_activity"
KEYBOARD_LISTENER_STATUS = "keyboard_listener_status"

# Payload kinds for KEYBOARD_ACTIVITY.
KEY_PRESS = "press"
KEY_RELEASE = "release"

# Linux input_event on 64-bit: timeval (2× long) + type + code + value.
_EVENT_FORMAT = "llHHI"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)
_EV_KEY = 0x01
_KEY_RELEASE_VALUE = 0
_KEY_PRESS_VALUE = 1
_KEY_REPEAT_VALUE = 2

_INPUT_BY_ID = Path("/dev/input/by-id")
_INPUT_BY_PATH = Path("/dev/input/by-path")
_PROC_DEVICES = Path("/proc/bus/input/devices")
_RESCAN_INTERVAL_SEC = 5.0


def discover_keyboard_device_paths(
    *,
    by_id: Path = _INPUT_BY_ID,
    by_path: Path = _INPUT_BY_PATH,
    proc_devices: Path = _PROC_DEVICES,
    input_root: Path = Path("/dev/input"),
) -> tuple[Path, ...]:
    """Return the best single keyboard event node (avoids duplicate press/release)."""
    found: list[Path] = []
    seen: set[Path] = set()

    for root, pattern in ((by_id, "*-event-kbd"), (by_path, "*-event-kbd")):
        if not root.is_dir():
            continue
        for link in sorted(root.glob(pattern)):
            try:
                resolved = link.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(link)

    if not found:
        for event_name in _event_names_from_proc(proc_devices):
            path = input_root / event_name
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            found.append(path)

    return select_primary_keyboard(tuple(found))


def select_primary_keyboard(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Pick one node so the same physical key is not reported twice."""
    if not paths:
        return ()
    # Extra HID interfaces are often ``…-if01-event-kbd`` (media/mouse).
    primary = [path for path in paths if "-if" not in path.name.casefold()]
    candidates = primary or list(paths)

    def score(path: Path) -> tuple[int, int, str]:
        name = path.name.casefold()
        return (
            0 if "keyboard" in name else 1,
            1 if "mouse" in name else 0,
            name,
        )

    candidates.sort(key=score)
    return (candidates[0],)


def _event_names_from_proc(proc_devices: Path) -> tuple[str, ...]:
    """Parse ``/proc/bus/input/devices`` for nodes that look like keyboards."""
    try:
        text = proc_devices.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()

    names: list[str] = []
    name = ""
    handlers = ""
    ev_bits = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if _looks_like_keyboard(name, handlers, ev_bits):
                event = _event_handler(handlers)
                if event is not None:
                    names.append(event)
            name = ""
            handlers = ""
            ev_bits = ""
            continue
        if line.startswith("N: Name="):
            name = line[8:].strip().strip('"')
        elif line.startswith("H: Handlers="):
            handlers = line[12:].strip()
        elif line.startswith("B: EV="):
            ev_bits = line[6:].strip()
    if _looks_like_keyboard(name, handlers, ev_bits):
        event = _event_handler(handlers)
        if event is not None:
            names.append(event)
    return tuple(names)


def _looks_like_keyboard(name: str, handlers: str, ev_bits: str) -> bool:
    if "kbd" not in handlers.split():
        return False
    if not _has_ev_key(ev_bits):
        return False
    lowered = name.casefold()
    if "keyboard" in lowered:
        return True
    # Real keyboards expose LEDs; many consumer-control interfaces do not.
    return "leds" in handlers.split()


def _has_ev_key(ev_bits: str) -> bool:
    try:
        return bool(int(ev_bits, 16) & _EV_KEY)
    except ValueError:
        return False


def _event_handler(handlers: str) -> str | None:
    for token in handlers.split():
        if token.startswith("event"):
            return token
    return None


def is_key_press_event(event_type: int, value: int) -> bool:
    """True only for the initial key-down (ignore repeats and releases)."""
    return event_type == _EV_KEY and value == _KEY_PRESS_VALUE


def is_key_release_event(event_type: int, value: int) -> bool:
    """True for key-up."""
    return event_type == _EV_KEY and value == _KEY_RELEASE_VALUE


class KeyboardActivityService:
    """Background reader that fans out key activity onto the EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._permission_warned = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="keyboard-activity",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            paths = discover_keyboard_device_paths()
            handles = self._open_devices(paths)
            if not handles:
                self._stop_event.wait(_RESCAN_INTERVAL_SEC)
                continue
            try:
                self._poll_devices(handles)
            finally:
                for handle in handles:
                    try:
                        handle.close()
                    except OSError:
                        pass

    def _open_devices(self, paths: Iterable[Path]) -> list:
        opened = []
        denied = False
        for path in paths:
            try:
                handle = open(path, "rb", buffering=0)
            except PermissionError:
                denied = True
                continue
            except OSError:
                continue
            try:
                flags = fcntl.fcntl(handle.fileno(), fcntl.F_GETFL)
                fcntl.fcntl(handle.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
            except OSError:
                handle.close()
                continue
            opened.append(handle)
        if opened:
            self._event_bus.emit(KEYBOARD_LISTENER_STATUS, "listening")
        elif denied:
            self._event_bus.emit(KEYBOARD_LISTENER_STATUS, "no_permission")
            if not self._permission_warned:
                self._permission_warned = True
                print(
                    "Jugoo keyboard cat: no read access to /dev/input "
                    "(sudo usermod -aG input \"$USER\" && re-login, "
                    "or: sg input -c jugoo)."
                )
        else:
            self._event_bus.emit(KEYBOARD_LISTENER_STATUS, "no_device")
        return opened

    def _poll_devices(self, handles: list) -> None:
        fileno_map = {handle.fileno(): handle for handle in handles}
        opened_at = time.monotonic()
        while not self._stop_event.is_set():
            try:
                ready, _, _ = select.select(list(fileno_map), [], [], 1.0)
            except (ValueError, OSError):
                return
            if time.monotonic() - opened_at >= _RESCAN_INTERVAL_SEC:
                # Drop and reopen so hot-plugged keyboards appear.
                return
            if not ready:
                continue
            for fd in ready:
                handle = fileno_map.get(fd)
                if handle is None:
                    continue
                for kind, code in self._drain_key_events(handle):
                    self._event_bus.emit(
                        KEYBOARD_ACTIVITY,
                        {"kind": kind, "code": code},
                    )

    def _drain_key_events(self, handle) -> list[tuple[str, int]]:
        events: list[tuple[str, int]] = []
        while True:
            try:
                raw = handle.read(_EVENT_SIZE)
            except BlockingIOError:
                break
            except OSError:
                break
            if not raw or len(raw) < _EVENT_SIZE:
                break
            _sec, _usec, event_type, code, value = struct.unpack(_EVENT_FORMAT, raw)
            if is_key_press_event(event_type, value):
                events.append((KEY_PRESS, int(code)))
            elif is_key_release_event(event_type, value):
                events.append((KEY_RELEASE, int(code)))
            # Key repeats (value=2) are ignored on purpose.
        return events
