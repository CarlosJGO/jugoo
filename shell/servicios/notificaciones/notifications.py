"""Session notification server backed by a persisted store and EventBus snapshots."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Any, Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from ...config import (
    NOTIFICATIONS_BUS_NAME,
    NOTIFICATIONS_CRITICAL_PERSIST,
    NOTIFICATIONS_CRITICAL_TIMEOUT_MS,
    NOTIFICATIONS_DEFAULT_TIMEOUT_MS,
    NOTIFICATIONS_DEV_BUS_NAME,
    NOTIFICATIONS_HISTORY_PATH,
    NOTIFICATIONS_ICON_CACHE_DIR,
    NOTIFICATIONS_INTERFACE,
    NOTIFICATIONS_MAX_HISTORY,
    NOTIFICATIONS_OBJECT_PATH,
    NOTIFICATIONS_PAUSED_DEFAULT,
    NOTIFICATIONS_PRODUCTION_RETRY_SEC,
)
from ...eventbus import EventBus
from ...models import NotificationAction, NotificationSnapshot
from ...runtime_paths import (
    migrate_legacy_file,
    notification_icons_dir,
    notifications_history_path,
)
from .notification_app import notification_app_key
from .notification_hints import normalize_hints, resolve_icon_fields, urgency_from_hints
from .notification_persistence import (
    load_history,
    save_history,
    shell_notifications_history_path,
    trim_history,
)

NOTIFICATIONS_CHANGED = "notifications_changed"
NOTIFICATION_RECEIVED = "notification_received"
NOTIFICATION_DISMISSED = "notification_dismissed"
NOTIFICATION_ACTION_INVOKED = "notification_action_invoked"
NOTIFICATIONS_PAUSED_CHANGED = "notifications_paused_changed"
NOTIFICATIONS_SOUND_MUTE_CHANGED = "notifications_sound_mute_changed"

CLOSE_REASON_EXPIRED = 1
CLOSE_REASON_DISMISS = 2
CLOSE_REASON_CLOSE_CALL = 3

_SERVER_CAPABILITIES = (
    "actions",
    "body",
    "body-markup",
    "icon-static",
    "persistence",
)
_SERVER_INFORMATION = ("shell", "waybar-shell", "0.1.0", "1.2")

_INTROSPECTION_XML = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/>
      <arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/>
      <arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/>
      <arg type="as" name="actions" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/>
      <arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
    <method name="CloseNotification">
      <arg type="u" name="id" direction="in"/>
    </method>
    <method name="GetCapabilities">
      <arg type="as" name="capabilities" direction="out"/>
    </method>
    <method name="GetNotifications">
      <arg type="aa{sv}" name="active_notifications" direction="out"/>
    </method>
    <method name="GetServerInformation">
      <arg type="s" name="name" direction="out"/>
      <arg type="s" name="vendor" direction="out"/>
      <arg type="s" name="version" direction="out"/>
      <arg type="s" name="spec_version" direction="out"/>
    </method>
    <method name="InvokeAction">
      <arg type="u" name="id" direction="in"/>
      <arg type="s" name="action_key" direction="in"/>
    </method>
    <signal name="ActionInvoked">
      <arg type="u" name="id"/>
      <arg type="s" name="action_key"/>
    </signal>
    <signal name="NotificationClosed">
      <arg type="u" name="id"/>
      <arg type="u" name="reason"/>
    </signal>
  </interface>
</node>
"""


def parse_notification_actions(flat_actions: list[str]) -> tuple[NotificationAction, ...]:
    actions: list[NotificationAction] = []
    index = 0
    while index + 1 < len(flat_actions):
        key = str(flat_actions[index]).strip()
        label = str(flat_actions[index + 1]).strip()
        if key:
            actions.append(NotificationAction(key=key, label=label))
        index += 2
    return tuple(actions)


class NotificationStore:
    """Notification history used by the D-Bus server and UI."""

    def __init__(self) -> None:
        self._items: dict[int, NotificationSnapshot] = {}
        self._next_id = 1

    @property
    def next_id(self) -> int:
        return self._next_id

    @property
    def snapshots(self) -> tuple[NotificationSnapshot, ...]:
        return tuple(self._items[id_] for id_ in sorted(self._items))

    @property
    def active_snapshots(self) -> tuple[NotificationSnapshot, ...]:
        """Live notifications still reported as active over D-Bus."""
        return tuple(
            item
            for item in self.snapshots
            if not item.dismissed and not item.expired
        )

    @property
    def history_snapshots(self) -> tuple[NotificationSnapshot, ...]:
        """Mailbox entries kept until explicitly deleted."""
        return tuple(item for item in self.snapshots if not item.dismissed)

    @property
    def unread_snapshots(self) -> tuple[NotificationSnapshot, ...]:
        """Unread mailbox entries; expiration does not imply read."""
        return tuple(
            item for item in self.history_snapshots if not item.read
        )

    def unread_count(self) -> int:
        return len(self.unread_snapshots)

    def get(self, notification_id: int) -> NotificationSnapshot | None:
        return self._items.get(notification_id)

    def restore(self, items: tuple[NotificationSnapshot, ...], next_id: int) -> None:
        self._items = {item.id: item for item in items}
        self._next_id = max(next_id, 1)
        if self._items:
            self._next_id = max(self._next_id, max(self._items) + 1)

    def add(
        self,
        *,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        icon_name: str,
        image_path: str,
        summary: str,
        body: str,
        actions: tuple[NotificationAction, ...],
        urgency: int,
        expire_timeout_ms: int,
        timestamp: float | None = None,
        read: bool = False,
        dismissed: bool = False,
        expired: bool = False,
    ) -> NotificationSnapshot:
        notification_id = (
            replaces_id if replaces_id and replaces_id in self._items else self._next_id
        )
        if notification_id == self._next_id:
            self._next_id += 1
        elif notification_id >= self._next_id:
            self._next_id = notification_id + 1

        snapshot = NotificationSnapshot(
            id=notification_id,
            app_name=app_name or "Application",
            app_icon=app_icon or "",
            icon_name=icon_name or "",
            image_path=image_path or "",
            summary=summary or "",
            body=body or "",
            actions=actions,
            urgency=urgency,
            timestamp=timestamp if timestamp is not None else time.time(),
            expire_timeout_ms=expire_timeout_ms,
            read=read,
            dismissed=dismissed,
            expired=expired,
        )
        self._items[notification_id] = snapshot
        return snapshot

    def expire(self, notification_id: int) -> NotificationSnapshot | None:
        item = self._items.get(notification_id)
        if item is None or item.dismissed or item.expired:
            return None
        updated = replace(item, expired=True)
        self._items[notification_id] = updated
        return updated

    def dismiss(self, notification_id: int) -> NotificationSnapshot | None:
        item = self._items.get(notification_id)
        if item is None or item.dismissed:
            return None
        updated = replace(item, dismissed=True, read=True)
        self._items[notification_id] = updated
        return updated

    def dismiss_all(self) -> tuple[NotificationSnapshot, ...]:
        dismissed: list[NotificationSnapshot] = []
        for notification_id in list(self._items):
            updated = self.dismiss(notification_id)
            if updated is not None:
                dismissed.append(updated)
        return tuple(dismissed)

    def mark_read(self, notification_id: int) -> NotificationSnapshot | None:
        item = self._items.get(notification_id)
        if item is None or item.read:
            return item
        updated = replace(item, read=True)
        self._items[notification_id] = updated
        return updated

    def mark_all_read(self) -> int:
        changed = 0
        for notification_id, item in list(self._items.items()):
            if item.dismissed or item.read:
                continue
            self._items[notification_id] = replace(item, read=True)
            changed += 1
        return changed

    def has_action(self, notification_id: int, action_key: str) -> bool:
        item = self._items.get(notification_id)
        if item is None or item.dismissed:
            return False
        return any(action.key == action_key for action in item.actions)

    def replace_all(self, items: tuple[NotificationSnapshot, ...]) -> None:
        self._items = {item.id: item for item in items}


class NotificationService:
    """Owns org.freedesktop.Notifications and exposes store snapshots to the UI."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        history_path: Path | None = None,
        icon_cache_dir: Path | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._shell_dir = Path(__file__).resolve().parent.parent
        self._history_path = history_path or notifications_history_path()
        if history_path is None:
            migrate_legacy_file(
                self._history_path,
                shell_notifications_history_path(self._shell_dir, NOTIFICATIONS_HISTORY_PATH),
            )
        self._icon_cache_dir = icon_cache_dir or notification_icons_dir()
        self._store = NotificationStore()
        self._paused = NOTIFICATIONS_PAUSED_DEFAULT
        self._sound_muted_apps: set[str] = set()
        self._bus: Gio.DBusConnection | None = None
        self._primary_owner_id = 0
        self._fallback_owner_id = 0
        self._registration_id = 0
        self._interface_info = Gio.DBusNodeInfo.new_for_xml(
            _INTROSPECTION_XML
        ).interfaces[0]
        self._started = False
        self._closing = False
        self._ready = False
        self._active_bus_name: str | None = None
        self._production_retry_source_id = 0
        self._expire_timers: dict[int, int] = {}

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active_bus_name(self) -> str | None:
        return self._active_bus_name

    @property
    def using_production_bus(self) -> bool:
        return self._active_bus_name == NOTIFICATIONS_BUS_NAME

    @property
    def snapshots(self) -> tuple[NotificationSnapshot, ...]:
        return self._store.snapshots

    @property
    def active_snapshots(self) -> tuple[NotificationSnapshot, ...]:
        return self._store.active_snapshots

    @property
    def history_snapshots(self) -> tuple[NotificationSnapshot, ...]:
        return self._store.history_snapshots

    @property
    def unread_count(self) -> int:
        return self._store.unread_count()

    @property
    def sound_muted_apps(self) -> tuple[str, ...]:
        return tuple(sorted(self._sound_muted_apps))

    def app_key_for(self, snapshot: NotificationSnapshot) -> str:
        return notification_app_key(
            app_name=snapshot.app_name,
            app_icon=snapshot.app_icon,
        )

    def is_app_sound_muted(self, app_key: str) -> bool:
        return str(app_key).casefold() in self._sound_muted_apps

    def is_snapshot_sound_muted(self, snapshot: NotificationSnapshot) -> bool:
        return self.is_app_sound_muted(self.app_key_for(snapshot))

    def should_play_sound(self, snapshot: NotificationSnapshot) -> bool:
        return not self.is_snapshot_sound_muted(snapshot)

    def set_app_sound_muted(self, app_key: str, *, muted: bool) -> bool:
        normalized = str(app_key).casefold().strip()
        if not normalized:
            return False
        if muted:
            if normalized in self._sound_muted_apps:
                return False
            self._sound_muted_apps.add(normalized)
        else:
            if normalized not in self._sound_muted_apps:
                return False
            self._sound_muted_apps.remove(normalized)
        self._persist_state()
        self._event_bus.emit(NOTIFICATIONS_SOUND_MUTE_CHANGED, self.sound_muted_apps)
        return True

    def toggle_app_sound_muted(self, app_key: str) -> bool:
        muted = self.is_app_sound_muted(app_key)
        self.set_app_sound_muted(app_key, muted=not muted)
        return not muted

    def get(self, notification_id: int) -> NotificationSnapshot | None:
        return self._store.get(notification_id)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._closing = False
        self._load_persisted_state()
        self._log(f"requesting bus name {NOTIFICATIONS_BUS_NAME!r}")
        self._request_primary_ownership()

    def close(self) -> None:
        self._closing = True
        self._stop_production_retry()
        self._clear_expire_timers()
        self._persist_state()
        self._unregister_object()
        if self._primary_owner_id:
            Gio.bus_unown_name(self._primary_owner_id)
            self._primary_owner_id = 0
        if self._fallback_owner_id:
            Gio.bus_unown_name(self._fallback_owner_id)
            self._fallback_owner_id = 0
        self._bus = None
        self._ready = False
        self._active_bus_name = None
        self._started = False

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._persist_state()
        self._event_bus.emit(NOTIFICATIONS_PAUSED_CHANGED, paused)
        self._log(f"notifications paused={'on' if paused else 'off'}")

    def toggle_paused(self) -> bool:
        self.set_paused(not self._paused)
        return self._paused

    def mark_read(self, notification_id: int) -> bool:
        item = self._store.get(notification_id)
        if item is None or item.dismissed or item.read:
            return False
        self._store.mark_read(notification_id)
        self._persist_state()
        self._emit_changed()
        return True

    def mark_all_read(self) -> int:
        changed = self._store.mark_all_read()
        if changed:
            self._persist_state()
            self._emit_changed()
        return changed

    def dismiss(self, notification_id: int) -> bool:
        self._cancel_expire_timer(notification_id)
        updated = self._store.dismiss(notification_id)
        if updated is None:
            return False
        self._persist_state()
        self._emit_dismissed(updated, CLOSE_REASON_CLOSE_CALL)
        return True

    def dismiss_all(self) -> int:
        dismissed = self._store.dismiss_all()
        for snapshot in dismissed:
            self._cancel_expire_timer(snapshot.id)
        if dismissed:
            self._persist_state()
            for snapshot in dismissed:
                self._emit_dismissed(snapshot, CLOSE_REASON_DISMISS)
        return len(dismissed)

    def expire(self, notification_id: int) -> bool:
        """Mark a notification expired without changing its read state."""
        self._cancel_expire_timer(notification_id)
        updated = self._store.expire(notification_id)
        if updated is None:
            return False
        self._persist_state()
        self._emit_closed(updated, CLOSE_REASON_EXPIRED)
        return True

    def invoke_action(self, notification_id: int, action_key: str) -> bool:
        if not self._store.has_action(notification_id, action_key):
            return False
        item = self._store.get(notification_id)
        if item is None:
            return False
        self._store.mark_read(notification_id)
        self._persist_state()
        self._emit_action_invoked(notification_id, action_key)
        self._emit_changed()
        return True

    def resolve_display_timeout_ms(self, snapshot: NotificationSnapshot) -> int:
        return self._resolve_expire_timeout_ms(snapshot)

    def _load_persisted_state(self) -> None:
        items, paused, next_id, sound_muted_apps = load_history(self._history_path)
        trimmed = trim_history(items, NOTIFICATIONS_MAX_HISTORY)
        self._store.restore(trimmed, next_id)
        self._paused = paused
        self._sound_muted_apps = set(sound_muted_apps)
        if trimmed != items:
            self._persist_state()
        if trimmed:
            self._log(f"loaded {len(trimmed)} persisted notifications")
        self._emit_changed()

    def _persist_state(self) -> None:
        trimmed = trim_history(self._store.snapshots, NOTIFICATIONS_MAX_HISTORY)
        if trimmed != self._store.snapshots:
            self._store.replace_all(trimmed)
        save_history(
            self._history_path,
            items=trimmed,
            paused=self._paused,
            next_id=self._store.next_id,
            sound_muted_apps=self._sound_muted_apps,
        )

    def _resolve_expire_timeout_ms(self, snapshot: NotificationSnapshot) -> int:
        timeout = snapshot.expire_timeout_ms
        if timeout == 0:
            if snapshot.urgency == 2 and NOTIFICATIONS_CRITICAL_PERSIST:
                return 0
            return NOTIFICATIONS_CRITICAL_TIMEOUT_MS
        if timeout > 0:
            return timeout
        return NOTIFICATIONS_DEFAULT_TIMEOUT_MS

    def _schedule_expiration(self, snapshot: NotificationSnapshot) -> None:
        timeout_ms = self._resolve_expire_timeout_ms(snapshot)
        if timeout_ms <= 0:
            return
        self._cancel_expire_timer(snapshot.id)

        def on_timeout(notification_id: int = snapshot.id) -> bool:
            self.expire(notification_id)
            return False

        source_id = GLib.timeout_add(timeout_ms, on_timeout)
        self._expire_timers[snapshot.id] = source_id

    def _cancel_expire_timer(self, notification_id: int) -> None:
        source_id = self._expire_timers.pop(notification_id, None)
        if source_id:
            GLib.source_remove(source_id)

    def _clear_expire_timers(self) -> None:
        for source_id in self._expire_timers.values():
            GLib.source_remove(source_id)
        self._expire_timers.clear()

    def _request_primary_ownership(self) -> None:
        if self._closing or self._primary_owner_id:
            return
        self._primary_owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            NOTIFICATIONS_BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_primary_name_acquired,
            self._on_primary_name_lost,
        )

    def _request_fallback_ownership(self) -> None:
        if self._closing or self._fallback_owner_id:
            return
        self._log(f"falling back to dev bus name {NOTIFICATIONS_DEV_BUS_NAME!r}")
        self._fallback_owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            NOTIFICATIONS_DEV_BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_fallback_name_acquired,
            self._on_fallback_name_lost,
        )

    def _on_bus_acquired(self, connection: Gio.DBusConnection, _name: str) -> None:
        self._bus = connection

    def _on_primary_name_acquired(
        self,
        _connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        self._log(f"acquired bus name {name!r}")
        self._stop_production_retry()
        if self._fallback_owner_id:
            self._log(f"releasing fallback bus {NOTIFICATIONS_DEV_BUS_NAME!r}")
            Gio.bus_unown_name(self._fallback_owner_id)
            self._fallback_owner_id = 0
        self._activate_bus_name(name)

    def _on_fallback_name_acquired(
        self,
        _connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        self._log(
            f"acquired fallback bus name {name!r} "
            f"(production name {NOTIFICATIONS_BUS_NAME!r} is unavailable)",
        )
        self._activate_bus_name(name)
        self._schedule_production_retry()

    def _on_primary_name_lost(
        self,
        _connection: Gio.DBusConnection | None,
        name: str,
    ) -> None:
        self._primary_owner_id = 0
        if self._closing:
            return

        if self._active_bus_name == name:
            self._log(f"lost bus name {name!r}")
            self._deactivate_bus_name()
            self._schedule_production_retry()

        if not self._fallback_owner_id and not self._active_bus_name:
            self._log(
                f"could not acquire {name!r}; another process owns "
                f"org.freedesktop.Notifications",
            )
            self._request_fallback_ownership()
            self._schedule_production_retry()

    def _on_fallback_name_lost(
        self,
        _connection: Gio.DBusConnection | None,
        name: str,
    ) -> None:
        self._fallback_owner_id = 0
        if self._closing:
            return
        if self._active_bus_name == name:
            self._log(f"lost fallback bus name {name!r}")
            self._deactivate_bus_name()

    def _activate_bus_name(self, name: str) -> None:
        if self._bus is None:
            return
        if self._active_bus_name == name and self._ready:
            return
        self._unregister_object()
        self._registration_id = self._bus.register_object(
            NOTIFICATIONS_OBJECT_PATH,
            self._interface_info,
            self._handle_method_call,
            None,
            None,
        )
        self._active_bus_name = name
        self._ready = True

    def _deactivate_bus_name(self) -> None:
        self._unregister_object()
        self._ready = False
        self._active_bus_name = None

    def _unregister_object(self) -> None:
        if self._bus is not None and self._registration_id:
            self._bus.unregister_object(self._registration_id)
            self._registration_id = 0

    def _schedule_production_retry(self) -> None:
        if (
            self._closing
            or self._production_retry_source_id
            or self._active_bus_name == NOTIFICATIONS_BUS_NAME
        ):
            return

        def retry() -> bool:
            if self._closing or self._active_bus_name == NOTIFICATIONS_BUS_NAME:
                self._production_retry_source_id = 0
                return False
            self._log(f"retrying acquisition of {NOTIFICATIONS_BUS_NAME!r}")
            self._request_primary_ownership()
            return True

        self._production_retry_source_id = GLib.timeout_add_seconds(
            NOTIFICATIONS_PRODUCTION_RETRY_SEC,
            retry,
        )

    def _stop_production_retry(self) -> None:
        if self._production_retry_source_id:
            GLib.source_remove(self._production_retry_source_id)
            self._production_retry_source_id = 0

    def _handle_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        try:
            if method_name == "Notify":
                self._method_notify(parameters, invocation)
            elif method_name == "CloseNotification":
                self._method_close_notification(parameters, invocation)
            elif method_name == "GetCapabilities":
                invocation.return_value(
                    GLib.Variant("(as)", (_SERVER_CAPABILITIES,))
                )
            elif method_name == "GetNotifications":
                invocation.return_value(
                    GLib.Variant(
                        "(aa{sv})",
                        ([
                            _snapshot_to_dbus_dict(item)
                            for item in self._store.active_snapshots
                        ],),
                    )
                )
            elif method_name == "GetServerInformation":
                invocation.return_value(
                    GLib.Variant("(ssss)", _SERVER_INFORMATION)
                )
            elif method_name == "InvokeAction":
                self._method_invoke_action(parameters, invocation)
            else:
                invocation.return_error(
                    Gio.DBusError.new(Gio.DBusError.UNKNOWN_METHOD, method_name)
                )
        except GLib.Error as error:
            invocation.return_gerror(error)

    def _method_notify(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        (
            app_name,
            replaces_id,
            app_icon,
            summary,
            body,
            actions,
            hints_variant,
            expire_timeout,
        ) = parameters.unpack()
        hints = normalize_hints(dict(hints_variant) if hints_variant else {})
        replaces_id = int(replaces_id or 0)
        provisional_id = replaces_id if replaces_id else self._store.next_id
        icon_name, image_path, normalized_app_icon = resolve_icon_fields(
            app_icon=str(app_icon or ""),
            hints=hints,
            notification_id=provisional_id,
            cache_dir=self._icon_cache_dir,
        )
        snapshot = self._store.add(
            app_name=str(app_name or ""),
            replaces_id=replaces_id,
            app_icon=normalized_app_icon,
            icon_name=icon_name,
            image_path=image_path,
            summary=str(summary or ""),
            body=str(body or ""),
            actions=parse_notification_actions(list(actions or [])),
            urgency=urgency_from_hints(hints),
            expire_timeout_ms=int(expire_timeout if expire_timeout is not None else -1),
        )
        self._persist_state()
        self._schedule_expiration(snapshot)
        self._log(
            f"Notify id={snapshot.id} app={snapshot.app_name!r} "
            f"summary={snapshot.summary!r}",
        )
        self._event_bus.emit(NOTIFICATION_RECEIVED, snapshot)
        self._emit_changed()
        invocation.return_value(GLib.Variant("(u)", (snapshot.id,)))

    def _method_close_notification(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        notification_id = int(parameters.unpack()[0])
        self._log(f"CloseNotification id={notification_id}")
        self._cancel_expire_timer(notification_id)
        updated = self._store.expire(notification_id)
        if updated is not None:
            self._persist_state()
            self._emit_closed(updated, CLOSE_REASON_CLOSE_CALL)
        invocation.return_value(None)

    def _method_invoke_action(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        notification_id, action_key = parameters.unpack()
        notification_id = int(notification_id)
        action_key = str(action_key)
        if not self.invoke_action(notification_id, action_key):
            invocation.return_error(
                Gio.DBusError.new(
                    Gio.DBusError.INVALID_ARGS,
                    f"unknown action {action_key!r} for notification {notification_id}",
                )
            )
            return
        invocation.return_value(None)

    def _emit_changed(self) -> None:
        self._event_bus.emit(NOTIFICATIONS_CHANGED, self._store.history_snapshots)

    def _emit_closed(self, snapshot: NotificationSnapshot, reason: int) -> None:
        self._emit_signal("NotificationClosed", GLib.Variant("(uu)", (snapshot.id, reason)))
        self._emit_changed()

    def _emit_dismissed(self, snapshot: NotificationSnapshot, reason: int) -> None:
        self._event_bus.emit(NOTIFICATION_DISMISSED, snapshot)
        self._emit_closed(snapshot, reason)

    def _emit_action_invoked(self, notification_id: int, action_key: str) -> None:
        payload = (notification_id, action_key)
        self._event_bus.emit(NOTIFICATION_ACTION_INVOKED, payload)
        self._emit_signal(
            "ActionInvoked",
            GLib.Variant("(us)", (notification_id, action_key)),
        )

    def _emit_signal(self, signal_name: str, parameters: GLib.Variant) -> None:
        if self._bus is None:
            return
        self._bus.emit_signal(
            None,
            NOTIFICATIONS_OBJECT_PATH,
            NOTIFICATIONS_INTERFACE,
            signal_name,
            parameters,
        )

    @staticmethod
    def _log(message: str) -> None:
        print(f"shell: notifications: {message}")


def _snapshot_to_dbus_dict(snapshot: NotificationSnapshot) -> dict[str, GLib.Variant]:
    flat_actions: list[str] = []
    for action in snapshot.actions:
        flat_actions.extend((action.key, action.label))
    return {
        "id": GLib.Variant("u", snapshot.id),
        "app_name": GLib.Variant("s", snapshot.app_name),
        "summary": GLib.Variant("s", snapshot.summary),
        "body": GLib.Variant("s", snapshot.body),
        "app_icon": GLib.Variant("s", snapshot.app_icon or snapshot.icon_name),
        "actions": GLib.Variant("as", flat_actions),
        "urgency": GLib.Variant("y", snapshot.urgency),
    }
