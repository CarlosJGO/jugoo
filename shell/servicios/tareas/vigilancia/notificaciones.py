"""Send reminders through the existing org.freedesktop.Notifications server."""

from __future__ import annotations

from typing import Callable

from ....config import NOTIFICATIONS_BUS_NAME, NOTIFICATIONS_INTERFACE, NOTIFICATIONS_OBJECT_PATH
from ....identity import APPLICATION_NAME, ICON_NAME
from .eventos import (
    TASK_WATCHER_INTERFACE,
    TASK_WATCHER_OBJECT_PATH,
    TASK_WATCHER_SIGNAL,
)

try:
    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError):
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]


ACTION_OPEN = "default"
ACTION_SNOOZE_SHORT = "snooze-15"
ACTION_SNOOZE_LONG = "snooze-60"
ACTION_DONE = "done"

REMINDER_ACTIONS = (
    ACTION_OPEN,
    "Abrir",
    ACTION_SNOOZE_SHORT,
    "15 min",
    ACTION_SNOOZE_LONG,
    "1 hora",
    ACTION_DONE,
    "Hecha",
)

ActionCallback = Callable[[int, str], None]


class DBusReminderNotifier:
    """Notification client. Jugoo already owns the session server."""

    def __init__(self) -> None:
        self._connection: Gio.DBusConnection | None = None
        self._subscription_id = 0
        self._on_action: ActionCallback | None = None
        self._known_ids: set[int] = set()

    def start(self, on_action: ActionCallback) -> None:
        self._on_action = on_action
        if Gio is None or GLib is None:
            return
        try:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._connection = None
            return
        self._subscription_id = self._connection.signal_subscribe(
            None,
            NOTIFICATIONS_INTERFACE,
            "ActionInvoked",
            NOTIFICATIONS_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_action_invoked,
        )

    def close(self) -> None:
        if self._connection is not None and self._subscription_id:
            self._connection.signal_unsubscribe(self._subscription_id)
        self._subscription_id = 0
        self._connection = None
        self._on_action = None
        self._known_ids.clear()

    def announce(self, kind: str) -> None:
        """Broadcast a one-word liveness pulse. Never blocks the watcher tick."""
        if self._connection is None or GLib is None:
            return
        try:
            self._connection.emit_signal(
                None,
                TASK_WATCHER_OBJECT_PATH,
                TASK_WATCHER_INTERFACE,
                TASK_WATCHER_SIGNAL,
                GLib.Variant("(s)", (str(kind),)),
            )
        except GLib.Error:
            return

    def notify(
        self,
        *,
        summary: str,
        body: str,
        urgency: int,
        expire_timeout_ms: int,
        task_id: str,
    ) -> int | None:
        if self._connection is None or GLib is None:
            return None
        hints = {
            "urgency": GLib.Variant("y", max(0, min(int(urgency), 2))),
            "desktop-entry": GLib.Variant("s", "com.jugoo.Shell"),
            "x-jugoo-task-id": GLib.Variant("s", task_id),
        }
        try:
            result = self._connection.call_sync(
                NOTIFICATIONS_BUS_NAME,
                NOTIFICATIONS_OBJECT_PATH,
                NOTIFICATIONS_INTERFACE,
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        APPLICATION_NAME,
                        0,
                        ICON_NAME,
                        summary,
                        body,
                        list(REMINDER_ACTIONS),
                        hints,
                        int(expire_timeout_ms),
                    ),
                ),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                4000,
                None,
            )
        except GLib.Error as error:
            print(f"shell: task-watcher: notify failed: {error}")
            return None
        notification_id = int(result.unpack()[0])
        self._known_ids.add(notification_id)
        return notification_id

    def open_tasks_panel(self) -> None:
        if self._connection is None or GLib is None:
            return
        try:
            self._connection.call_sync(
                "com.jugoo.Shell",
                "/com/jugoo/Shell",
                "org.freedesktop.Application",
                "ActivateAction",
                GLib.Variant("(sava{sv})", ("open-tasks", [], {})),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
        except GLib.Error:
            return

    def _on_action_invoked(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        _signal_name: str,
        parameters: GLib.Variant,
    ) -> None:
        notification_id, action_key = parameters.unpack()
        notification_id = int(notification_id)
        if notification_id not in self._known_ids:
            return
        callback = self._on_action
        if callback is not None:
            callback(notification_id, str(action_key))
