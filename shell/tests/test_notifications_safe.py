"""Safe tests for the notification server (store + D-Bus roundtrip + persistence)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gio, GLib, Gtk

from shell.config import NOTIFICATIONS_MAX_VISIBLE_TOASTS
from shell.eventbus import EventBus
from shell.models import NotificationSnapshot
from shell.servicios.notificaciones.notification_hints import urgency_from_hints
from shell.servicios.notificaciones.notification_app import notification_app_key
from shell.servicios.notificaciones.notification_persistence import (
    load_history,
    save_history,
    trim_history,
)
from shell.servicios.notificaciones.notifications import (
    CLOSE_REASON_CLOSE_CALL,
    NotificationService,
    NotificationStore,
    parse_notification_actions,
)
from shell.widgets.notificaciones.notification_toast import NotificationToast
from shell.widgets.notificaciones.notification_toast_manager import NotificationToastManager
from shell.widgets.notificaciones.toast_presentation_queue import ToastPresentationQueue


def _add(
    store: NotificationStore,
    *,
    summary: str = "Summary",
    body: str = "",
    replaces_id: int = 0,
    urgency: int = 1,
    read: bool = False,
    dismissed: bool = False,
) -> NotificationSnapshot:
    return store.add(
        app_name="TestApp",
        replaces_id=replaces_id,
        app_icon="dialog-information",
        icon_name="dialog-information",
        image_path="",
        summary=summary,
        body=body,
        actions=(),
        urgency=urgency,
        expire_timeout_ms=-1,
        read=read,
        dismissed=dismissed,
    )


def test_parse_notification_actions() -> None:
    actions = parse_notification_actions(["default", "Open", "ignore", "Ignore"])
    assert [(action.key, action.label) for action in actions] == [
        ("default", "Open"),
        ("ignore", "Ignore"),
    ]


def test_urgency_from_hints_defaults_to_normal() -> None:
    assert urgency_from_hints({}) == 1
    assert urgency_from_hints({"urgency": GLib.Variant("y", 2)}) == 2


def test_store_add_dismiss_and_unread_count() -> None:
    store = NotificationStore()
    first = _add(store, body="World")
    assert first.id == 1
    assert store.unread_count() == 1
    assert len(store.active_snapshots) == 1
    assert len(store.history_snapshots) == 1

    dismissed = store.dismiss(first.id)
    assert dismissed is not None
    assert dismissed.dismissed is True
    assert store.unread_count() == 0
    assert store.active_snapshots == ()
    assert store.history_snapshots == ()
    assert len(store.snapshots) == 1


def test_store_expire_keeps_history() -> None:
    store = NotificationStore()
    item = _add(store, summary="Expire me")
    assert store.unread_count() == 1
    assert len(store.history_snapshots) == 1

    expired = store.expire(item.id)
    assert expired is not None
    assert expired.expired is True
    assert expired.dismissed is False
    assert store.active_snapshots == ()
    assert len(store.history_snapshots) == 1
    assert store.unread_count() == 1


def test_store_mark_read_decreases_unread() -> None:
    store = NotificationStore()
    item = _add(store, summary="Unread")
    assert store.unread_count() == 1
    updated = store.mark_read(item.id)
    assert updated is not None
    assert updated.read is True
    assert store.unread_count() == 0
    assert len(store.history_snapshots) == 1


def test_store_ten_notifications_unread_count() -> None:
    store = NotificationStore()
    for index in range(10):
        _add(store, summary=f"Notice {index + 1}")
    assert store.unread_count() == 10
    assert len(store.history_snapshots) == 10


def test_store_expire_does_not_mark_read() -> None:
    store = NotificationStore()
    item = _add(store, summary="Still unread")
    store.expire(item.id)
    assert store.unread_count() == 1
    store.mark_read(item.id)
    assert store.unread_count() == 0


def test_persistence_preserves_unread_after_expire() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "notifications.json"
        store = NotificationStore()
        item = _add(store, summary="Persist unread")
        store.expire(item.id)
        assert store.unread_count() == 1
        save_history(path, items=store.snapshots, paused=False, next_id=store.next_id)

        restored_store = NotificationStore()
        loaded, paused, next_id, muted = load_history(path)
        restored_store.restore(loaded, next_id)
        assert paused is False
        assert muted == frozenset()
        assert restored_store.unread_count() == 1
        assert restored_store.history_snapshots[0].expired is True
        assert restored_store.history_snapshots[0].read is False


def test_toast_queue_respects_max_visible() -> None:
    queue = ToastPresentationQueue(NOTIFICATIONS_MAX_VISIBLE_TOASTS)
    assert queue.max_visible == 3
    for notification_id in range(1, 11):
        queue.enqueue(notification_id)
    promoted = queue.promote()
    assert len(promoted) == 3
    assert queue.visible_count() == 3
    assert queue.pending_count() == 7
    assert queue.visible_ids == (1, 2, 3)


def test_toast_queue_fifo_promotion_on_release() -> None:
    queue = ToastPresentationQueue(3)
    for notification_id in range(1, 11):
        queue.enqueue(notification_id)
    queue.promote()
    assert queue.visible_ids == (1, 2, 3)

    queue.release(1)
    promoted = queue.promote()
    assert promoted == ((0, 4),)
    assert queue.visible_ids == (4, 2, 3)
    assert queue.pending_count() == 6

    queue.release(2)
    queue.release(3)
    queue.release(4)
    promoted = queue.promote()
    assert [notification_id for _, notification_id in promoted] == [5, 6, 7]
    assert queue.visible_ids == (5, 6, 7)
    assert queue.pending_count() == 3
    assert queue.pending_ids == (8, 9, 10)


def test_toast_queue_clear_drops_pending_without_losing_ids() -> None:
    queue = ToastPresentationQueue(3)
    for notification_id in range(1, 6):
        queue.enqueue(notification_id)
    queue.promote()
    cancelled = queue.clear()
    assert cancelled == (1, 2, 3)
    assert queue.pending_count() == 0
    assert queue.visible_count() == 0


def test_toast_queue_does_not_duplicate_tracked_ids() -> None:
    queue = ToastPresentationQueue(3)
    queue.enqueue(1)
    queue.enqueue(1)
    queue.promote()
    queue.enqueue(1)
    assert queue.pending_count() == 0
    assert queue.visible_count() == 1


def test_service_expire_keeps_unread_count() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()
        snapshot = service._store.add(
            app_name="App",
            replaces_id=0,
            app_icon="",
            icon_name="",
            image_path="",
            summary="Expire",
            body="",
            actions=(),
            urgency=1,
            expire_timeout_ms=-1,
        )
        service._persist_state()
        assert service.unread_count == 1
        assert service.expire(snapshot.id) is True
        assert service.unread_count == 1
        assert service.active_snapshots == ()
        assert len(service.history_snapshots) == 1
        assert service.mark_read(snapshot.id) is True
        assert service.unread_count == 0
        service.close()


def test_store_replace_by_id() -> None:
    store = NotificationStore()
    first = _add(store, summary="One")
    second = _add(store, summary="Two", replaces_id=first.id)
    assert second.id == first.id
    assert second.summary == "Two"
    assert len(store.snapshots) == 1


def test_store_mark_all_read() -> None:
    store = NotificationStore()
    _add(store, summary="One")
    _add(store, summary="Two")
    assert store.unread_count() == 2
    changed = store.mark_all_read()
    assert changed == 2
    assert store.unread_count() == 0


def test_store_invoke_action_tracking() -> None:
    store = NotificationStore()
    item = store.add(
        app_name="App",
        replaces_id=0,
        app_icon="",
        icon_name="",
        image_path="",
        summary="Action",
        body="",
        actions=parse_notification_actions(["reply", "Reply"]),
        urgency=1,
        expire_timeout_ms=-1,
    )
    assert store.has_action(item.id, "reply") is True
    assert store.has_action(item.id, "missing") is False


def test_trim_history_prefers_active_items() -> None:
    items = (
        NotificationSnapshot(
            id=1,
            app_name="A",
            app_icon="",
            summary="old dismissed",
            body="",
            actions=(),
            urgency=1,
            timestamp=1.0,
            expire_timeout_ms=-1,
            read=True,
            dismissed=True,
        ),
        NotificationSnapshot(
            id=2,
            app_name="B",
            app_icon="",
            summary="active",
            body="",
            actions=(),
            urgency=1,
            timestamp=2.0,
            expire_timeout_ms=-1,
        ),
    )
    trimmed = trim_history(items, 1)
    assert len(trimmed) == 1
    assert trimmed[0].id == 2


def test_persistence_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "notifications.json"
        snapshot = NotificationSnapshot(
            id=7,
            app_name="Persist",
            app_icon="app",
            icon_name="icon",
            image_path="/tmp/icon.png",
            summary="Saved",
            body="Body",
            actions=parse_notification_actions(["default", "Open"]),
            urgency=2,
            timestamp=1234.5,
            expire_timeout_ms=5000,
            read=False,
            dismissed=False,
            expired=False,
        )
        save_history(path, items=(snapshot,), paused=True, next_id=8, sound_muted_apps=frozenset({"strawberry"}))
        loaded, paused, next_id, muted = load_history(path)
        assert paused is True
        assert next_id == 8
        assert muted == frozenset({"strawberry"})
        assert len(loaded) == 1
        assert loaded[0].summary == "Saved"
        assert loaded[0].icon_name == "icon"
        assert loaded[0].actions[0].key == "default"
        assert loaded[0].expired is False

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert payload["sound_muted_apps"] == ["strawberry"]


def _pump_context(max_iterations: int = 200) -> None:
    context = GLib.MainContext.default()
    for _ in range(max_iterations):
        while context.iteration(False):
            pass
        GLib.usleep(10000)


def _call_dbus_while_pumping(callable_fn) -> object:
    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def worker() -> None:
        try:
            result["value"] = callable_fn()
        except BaseException as exc:  # pragma: no cover - surfaced below
            error["value"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while thread.is_alive() and time.time() < deadline:
        _pump_context(10)
    thread.join(timeout=1)
    if thread.is_alive():
        raise TimeoutError("D-Bus call did not complete")
    if "value" in error:
        raise error["value"]
    return result.get("value")


def test_dbus_notify_dismiss_and_action() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        event_bus = EventBus()
        received: list[int] = []
        dismissed: list[int] = []
        invoked: list[tuple[int, str]] = []

        event_bus.subscribe(
            "notification_received",
            lambda snapshot: received.append(snapshot.id),
        )
        event_bus.subscribe(
            "notification_dismissed",
            lambda snapshot: dismissed.append(snapshot.id),
        )
        event_bus.subscribe(
            "notification_action_invoked",
            lambda payload: invoked.append(payload),
        )

        service = NotificationService(event_bus, history_path=history_path)
        service.start()

        for _ in range(500):
            _pump_context(1)
            if service.ready:
                break

        assert service.ready, "notification bus name was not acquired"
        assert service.active_bus_name in {
            "org.freedesktop.Notifications",
            "org.freedesktop.Notifications.ShellDev",
        }
        assert service.active_bus_name is not None
        bus_name = service.active_bus_name

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        notify_variant = GLib.Variant(
            "(susssasa{sv}i)",
            (
                "ShellTest",
                0,
                "dialog-information",
                "Test summary",
                "Test body",
                ["default", "Open"],
                {"urgency": GLib.Variant("y", 1)},
                -1,
            ),
        )

        def notify_call() -> int:
            result = bus.call_sync(
                bus_name,
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                notify_variant,
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                5000,
            )
            return int(result.unpack()[0])

        notification_id = int(_call_dbus_while_pumping(notify_call))
        assert notification_id > 0
        assert received == [notification_id]
        assert service.unread_count == 1
        assert history_path.is_file()

        service.mark_all_read()
        assert service.unread_count == 0

        def invoke_call() -> None:
            bus.call_sync(
                bus_name,
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "InvokeAction",
                GLib.Variant("(us)", (notification_id, "default")),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
            )

        _call_dbus_while_pumping(invoke_call)
        _pump_context(20)
        assert invoked == [(notification_id, "default")]

        def close_call() -> None:
            bus.call_sync(
                bus_name,
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "CloseNotification",
                GLib.Variant("(u)", (notification_id,)),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
            )

        _call_dbus_while_pumping(close_call)
        _pump_context(20)
        assert dismissed == []
        assert service.active_snapshots == ()
        assert len(service.history_snapshots) == 1
        assert service.history_snapshots[0].expired is True
        assert service.history_snapshots[0].dismissed is False

        restored, paused, _next_id, muted = load_history(history_path)
        assert paused is False
        assert muted == frozenset()
        assert any(item.id == notification_id and item.expired for item in restored)

        service.dismiss(notification_id)
        _pump_context(20)
        assert dismissed == [notification_id]
        assert service.history_snapshots == ()

        service.close()


def test_service_timeout_expires_but_keeps_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        event_bus = EventBus()
        service = NotificationService(event_bus, history_path=history_path)
        service.start()

        for _ in range(500):
            _pump_context(1)
            if service.ready:
                break
        assert service.ready

        bus_name = service.active_bus_name
        assert bus_name is not None
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        notify_variant = GLib.Variant(
            "(susssasa{sv}i)",
            (
                "TimeoutTest",
                0,
                "dialog-information",
                "Short lived",
                "Body",
                [],
                {},
                100,
            ),
        )

        def notify_call() -> int:
            result = bus.call_sync(
                bus_name,
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                notify_variant,
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                5000,
            )
            return int(result.unpack()[0])

        notification_id = int(_call_dbus_while_pumping(notify_call))
        assert len(service.history_snapshots) == 1
        assert service.unread_count == 1

        deadline = time.time() + 3
        while time.time() < deadline:
            _pump_context(20)
            if not service.active_snapshots:
                break

        assert service.active_snapshots == ()
        assert len(service.history_snapshots) == 1
        assert service.history_snapshots[0].expired is True
        assert service.unread_count == 1
        restored, _, _, _ = load_history(history_path)
        assert any(item.id == notification_id and item.expired for item in restored)
        assert any(item.id == notification_id and not item.read for item in restored)

        service.close()


def test_toast_timeout_flow_expires_without_mark_read() -> None:
    """Simulates toast timeout: expire() only, unread unchanged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()
        snapshot = _add(service._store, summary="Toast timeout")
        assert service.unread_count == 1
        assert service.expire(snapshot.id) is True
        assert service.unread_count == 1
        service.close()


def test_toast_click_flow_marks_read_without_actions() -> None:
    """Simulates toast click with no actions: mark_read lowers unread."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()
        snapshot = _add(service._store, summary="Toast click")
        assert service.unread_count == 1
        assert service.mark_read(snapshot.id) is True
        assert service.unread_count == 0
        service.close()


def test_popup_open_clears_queue_without_touching_history() -> None:
    """Simulates opening history: pending/visible queue cleared, unread intact."""
    queue = ToastPresentationQueue(NOTIFICATIONS_MAX_VISIBLE_TOASTS)
    store = NotificationStore()
    for index in range(1, 11):
        _add(store, summary=f"Notice {index}")
        queue.enqueue(index)
    queue.promote()
    assert store.unread_count() == 10
    assert queue.visible_count() == 3
    assert queue.pending_count() == 7

    queue.clear()
    assert queue.visible_count() == 0
    assert queue.pending_count() == 0
    assert store.unread_count() == 10
    assert len(store.history_snapshots) == 10


def test_notification_app_key_uses_stable_app_name() -> None:
    assert notification_app_key(app_name="Strawberry", app_icon="") == "strawberry"
    assert notification_app_key(app_name="Firefox", app_icon="") == "firefox"
    assert notification_app_key(app_name="", app_icon="discord.desktop") == "discord"


def test_service_should_play_sound_respects_muted_apps() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()
        normal_snapshot = service._store.add(
            app_name="Firefox",
            replaces_id=0,
            app_icon="",
            icon_name="",
            image_path="",
            summary="Tab update",
            body="",
            actions=(),
            urgency=1,
            expire_timeout_ms=-1,
        )
        muted_snapshot = service._store.add(
            app_name="Strawberry",
            replaces_id=0,
            app_icon="",
            icon_name="",
            image_path="",
            summary="Now playing",
            body="",
            actions=(),
            urgency=1,
            expire_timeout_ms=-1,
        )
        service.set_app_sound_muted("strawberry", muted=True)
        assert service.should_play_sound(normal_snapshot) is True
        assert service.should_play_sound(muted_snapshot) is False
        service.set_app_sound_muted("strawberry", muted=False)
        assert service.should_play_sound(muted_snapshot) is True
        service.close()


def test_sound_muted_apps_persist_after_restart() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()
        service.set_app_sound_muted("Strawberry", muted=True)
        service.close()

        reloaded = NotificationService(EventBus(), history_path=history_path)
        reloaded.start()
        assert reloaded.is_app_sound_muted("strawberry") is True
        assert reloaded.sound_muted_apps == ("strawberry",)
        reloaded.close()


def test_muted_app_still_records_notification_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()
        service.set_app_sound_muted("Strawberry", muted=True)
        snapshot = service._store.add(
            app_name="Strawberry",
            replaces_id=0,
            app_icon="",
            icon_name="",
            image_path="",
            summary="Track",
            body="Artist",
            actions=(),
            urgency=1,
            expire_timeout_ms=-1,
        )
        service._persist_state()
        assert service.should_play_sound(snapshot) is False
        assert len(service.history_snapshots) == 1
        assert service.unread_count == 1
        service.close()


def test_toast_manager_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "notifications.json"
        service = NotificationService(EventBus(), history_path=history_path)
        service.start()

        shell_win = Gtk.Window()
        btn = Gtk.Button()
        invoked: list[tuple[int, str]] = []
        read_ids: list[int] = []

        mgr = NotificationToastManager(
            shell_win,
            service,
            btn,
            on_invoke_action=lambda nid, k: invoked.append((nid, k)),
            on_mark_read=lambda nid: read_ids.append(nid),
            max_visible=3,
        )

        s1 = _add(service._store, summary="Toast 1", body="Body 1")
        s2 = _add(service._store, summary="Toast 2", body="Body 2")
        s3 = _add(service._store, summary="Toast 3", body="Body 3")
        s4 = _add(service._store, summary="Toast 4", body="Body 4")

        mgr.enqueue(s1)
        mgr.enqueue(s2)
        mgr.enqueue(s3)
        mgr.enqueue(s4)

        assert mgr.visible_count == 3
        assert mgr.pending_count == 1
        assert len(mgr._toasts) == 3
        assert len(mgr._layer._box.get_children()) == 3

        # Dismiss toast 1
        mgr._toasts[s1.id].dismiss("click")
        assert s1.id in read_ids
        assert mgr.visible_count == 3
        assert mgr.pending_count == 0
        assert s4.id in mgr._toasts
        assert len(mgr._layer._box.get_children()) == 3

        # Updating an existing notification
        s2_updated = _add(service._store, summary="Toast 2 updated", replaces_id=s2.id)
        mgr.enqueue(s2_updated)
        assert mgr.visible_count == 3
        assert len(mgr._toasts) == 3

        mgr.clear_presentations()
        assert mgr.visible_count == 0
        assert len(mgr._toasts) == 0
        assert len(mgr._layer._box.get_children()) == 0

        mgr.destroy()
        service.close()


if __name__ == "__main__":
    test_parse_notification_actions()
    test_urgency_from_hints_defaults_to_normal()
    test_store_add_dismiss_and_unread_count()
    test_store_expire_keeps_history()
    test_store_mark_read_decreases_unread()
    test_store_ten_notifications_unread_count()
    test_store_expire_does_not_mark_read()
    test_persistence_preserves_unread_after_expire()
    test_toast_queue_respects_max_visible()
    test_toast_queue_fifo_promotion_on_release()
    test_toast_queue_clear_drops_pending_without_losing_ids()
    test_toast_queue_does_not_duplicate_tracked_ids()
    test_service_expire_keeps_unread_count()
    test_store_replace_by_id()
    test_store_mark_all_read()
    test_store_invoke_action_tracking()
    test_trim_history_prefers_active_items()
    test_persistence_roundtrip()
    test_dbus_notify_dismiss_and_action()
    test_service_timeout_expires_but_keeps_history()
    test_toast_timeout_flow_expires_without_mark_read()
    test_toast_click_flow_marks_read_without_actions()
    test_popup_open_clears_queue_without_touching_history()
    test_notification_app_key_uses_stable_app_name()
    test_service_should_play_sound_respects_muted_apps()
    test_sound_muted_apps_persist_after_restart()
    test_muted_app_still_records_notification_history()
    test_toast_manager_lifecycle()
    print("notification safe tests OK")
