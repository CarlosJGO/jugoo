"""Watcher liveness state and a D-Bus → EventBus bridge for the shell."""

from __future__ import annotations

from ...eventbus import EventBus
from .vigilancia.eventos import (
    KIND_AI_REMINDER,
    KIND_HEARTBEAT,
    KIND_REMINDER,
    TASK_WATCHER_INTERFACE,
    TASK_WATCHER_OBJECT_PATH,
    TASK_WATCHER_SIGNAL,
    event_name_for_kind,
)

try:
    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError):
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]


STATUS_UNKNOWN = "unknown"
STATUS_ALIVE = "alive"
STATUS_QUIET = "quiet"
STATUS_INACTIVE = "inactive"


class WatcherPresence:
    """In-memory liveness. Starts unknown until a real heartbeat arrives."""

    def __init__(self) -> None:
        self.last_heartbeat_at: float | None = None
        self.status = STATUS_UNKNOWN
        self.last_kind = KIND_HEARTBEAT

    def note(self, kind: str, now: float) -> str:
        pulse = kind if kind in {KIND_HEARTBEAT, KIND_REMINDER, KIND_AI_REMINDER} else KIND_HEARTBEAT
        self.last_heartbeat_at = now
        self.last_kind = pulse
        self.status = STATUS_ALIVE
        return pulse

    def mark_quiet(self) -> bool:
        if self.status != STATUS_ALIVE:
            return False
        self.status = STATUS_QUIET
        return True

    def mark_inactive(self) -> bool:
        if self.status in {STATUS_UNKNOWN, STATUS_INACTIVE}:
            return False
        self.status = STATUS_INACTIVE
        return True


class TaskWatcherBridge:
    """Receives the watcher's session-bus pulse and republishes it on EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._connection: Gio.DBusConnection | None = None
        self._subscription_id = 0

    def start(self) -> None:
        if Gio is None or GLib is None or self._subscription_id:
            return
        try:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._connection = None
            return
        self._subscription_id = self._connection.signal_subscribe(
            None,
            TASK_WATCHER_INTERFACE,
            TASK_WATCHER_SIGNAL,
            TASK_WATCHER_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_signal,
        )

    def close(self) -> None:
        if self._connection is not None and self._subscription_id:
            self._connection.signal_unsubscribe(self._subscription_id)
        self._subscription_id = 0
        self._connection = None

    def dispatch(self, kind: str) -> None:
        self._event_bus.emit(event_name_for_kind(kind), kind)

    def _on_signal(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        _signal_name: str,
        parameters: GLib.Variant,
    ) -> None:
        kind = str(parameters.unpack()[0])
        self.dispatch(kind)
