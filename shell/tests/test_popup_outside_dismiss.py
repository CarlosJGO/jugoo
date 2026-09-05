"""Tests for PopupOutsideDismiss pointer-driven outside close."""

from __future__ import annotations

from unittest import mock

import gi

gi.require_version("GLib", "2.0")

from shell.config import POPUP_OUTSIDE_DISMISS_GRACE_MS
from shell.models import ActiveWindow
from shell.popup_handle import PopupOutsideDismiss, present_popup
from shell.servicios.escritorio.hyprland import ACTIVE_WINDOW_CHANGED


class _FakeEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def subscribe(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler) -> None:
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def emit(self, event: str, payload) -> None:
        for handler in list(self._handlers.get(event, ())):
            handler(payload)


class _Connectable:
    _next_id = 1

    def __init__(self, *, visible: bool = True, title: str = "Shell Test Popup") -> None:
        self._visible = visible
        self._title = title
        self._opacity = 1.0
        self._handlers: dict[str, dict[int, object]] = {}
        self.present_calls = 0

    def connect(self, signal: str, handler) -> int:
        handler_id = _Connectable._next_id
        _Connectable._next_id += 1
        self._handlers.setdefault(signal, {})[handler_id] = handler
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        for _signal, handlers in self._handlers.items():
            if handler_id in handlers:
                del handlers[handler_id]
                return

    def emit(self, signal: str, *args) -> None:
        for handler in list(self._handlers.get(signal, {}).values()):
            handler(self, *args)

    def get_visible(self) -> bool:
        return self._visible

    def has_focus(self) -> bool:
        return False

    def get_title(self) -> str:
        return self._title

    def set_opacity(self, value: float) -> None:
        self._opacity = value

    def get_opacity(self) -> float:
        return self._opacity

    def show_all(self) -> None:
        self._visible = True

    def present(self) -> None:
        self.present_calls += 1

    def hide(self) -> None:
        self._visible = False


class _FakeTimers:
    def __init__(self) -> None:
        self.next_id = 1
        self.active: dict[int, tuple[int, object, tuple]] = {}
        self.removed: list[int] = []

    def timeout_add(self, ms: int, callback, *args) -> int:
        timer_id = self.next_id
        self.next_id += 1
        self.active[timer_id] = (ms, callback, args)
        return timer_id

    def source_remove(self, source_id: int) -> None:
        self.removed.append(source_id)
        self.active.pop(source_id, None)

    def fire(self, source_id: int):
        _ms, callback, args = self.active.pop(source_id)
        return callback(*args)


def _install_dismiss(
    dismiss: PopupOutsideDismiss,
    *,
    popup: _Connectable | None = None,
    anchor: _Connectable | None = None,
) -> tuple[list[bool], _FakeEventBus, PopupOutsideDismiss]:
    closed: list[bool] = []
    bus = _FakeEventBus()
    popup = popup or _Connectable()
    shell = _Connectable(title="Shell")
    anchor = anchor or _Connectable(title="Anchor")
    dismiss.install(popup, shell, (anchor,), lambda: closed.append(True), bus)
    dismiss._install_grace_until = 0
    return closed, bus, dismiss


def _other_window() -> ActiveWindow:
    return ActiveWindow(
        address="0xabc",
        app_class="other",
        application_name="Other",
        title="Other App",
        icon="",
    )


def test_leave_does_not_start_timer_while_pointer_inside() -> None:
    closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=True):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())

    assert closed == []
    assert dismiss._deferred_dismiss_source_id == 0
    assert timers.active == {}


def test_enter_leave_dismisses_after_grace() -> None:
    closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("enter-notify-event", object())
                popup.emit("leave-notify-event", object())
                timer_id = dismiss._deferred_dismiss_source_id
                assert closed == []
                assert timer_id != 0
                assert timers.active[timer_id][0] == POPUP_OUTSIDE_DISMISS_GRACE_MS
                timers.fire(timer_id)

    assert closed == [True]
    assert dismiss._popup is None
    assert dismiss._deferred_dismiss_source_id == 0
    assert timers.active == {}


def test_enter_leave_enter_before_grace_does_not_dismiss() -> None:
    closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("enter-notify-event", object())
                popup.emit("leave-notify-event", object())
                timer_id = dismiss._deferred_dismiss_source_id
                popup.emit("enter-notify-event", object())
                assert dismiss._deferred_dismiss_source_id == 0
                assert timers.removed == [timer_id]
                assert timers.active == {}

    assert closed == []
    assert dismiss._popup is not None


def test_leave_then_focus_out_still_dismisses() -> None:
    closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                first_id = dismiss._deferred_dismiss_source_id
                popup.emit("focus-out-event", object())
                assert dismiss._deferred_dismiss_source_id == first_id
                assert list(timers.active) == [first_id]
                timers.fire(first_id)

    assert closed == [True]


def test_leave_then_focus_out_then_click_other_window_still_dismisses() -> None:
    closed, bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                first_id = dismiss._deferred_dismiss_source_id
                popup.emit("focus-out-event", object())
                popup.emit("focus-in-event", object())
                bus.emit(ACTIVE_WINDOW_CHANGED, _other_window())
                assert dismiss._deferred_dismiss_source_id == first_id
                assert list(timers.active) == [first_id]
                timers.fire(first_id)

    assert closed == [True]


def test_leave_then_content_refresh_still_dismisses() -> None:
    closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                timer_id = dismiss._deferred_dismiss_source_id
                present_popup(popup)
                assert popup.present_calls == 0
                assert dismiss._deferred_dismiss_source_id == timer_id
                assert list(timers.active) == [timer_id]
                timers.fire(timer_id)

    assert closed == [True]


def test_leave_enter_leave_starts_new_timer() -> None:
    closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                first_id = dismiss._deferred_dismiss_source_id
                popup.emit("enter-notify-event", object())
                popup.emit("leave-notify-event", object())
                second_id = dismiss._deferred_dismiss_source_id
                assert first_id != 0
                assert second_id != 0
                assert second_id != first_id
                assert first_id in timers.removed
                assert first_id not in timers.active
                assert list(timers.active) == [second_id]

    assert closed == []


def test_focus_out_does_not_cancel_or_replace_pointer_timer() -> None:
    _closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                first_id = dismiss._deferred_dismiss_source_id
                popup.emit("focus-out-event", object())
                popup.emit("focus-out-event", object())

    assert dismiss._deferred_dismiss_source_id == first_id
    assert timers.removed == []
    assert list(timers.active) == [first_id]


def test_stale_timer_cannot_close_reopened_popup() -> None:
    first = PopupOutsideDismiss()
    closed_first, _bus, dismiss = _install_dismiss(first)
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                stale_id = dismiss._deferred_dismiss_source_id
                stale = timers.active[stale_id]
                dismiss.uninstall()
                assert stale_id in timers.removed
                assert dismiss._deferred_dismiss_source_id == 0

                closed_second: list[bool] = []
                bus = _FakeEventBus()
                new_popup = _Connectable(title="Reopened")
                shell = _Connectable(title="Shell")
                anchor = _Connectable(title="Anchor")
                dismiss.install(
                    new_popup,
                    shell,
                    (anchor,),
                    lambda: closed_second.append(True),
                    bus,
                )
                dismiss._install_grace_until = 0
                new_popup.emit("leave-notify-event", object())
                live_id = dismiss._deferred_dismiss_source_id
                _ms, stale_callback, stale_args = stale
                stale_callback(*stale_args)

    assert closed_first == []
    assert closed_second == []
    assert dismiss._popup is new_popup
    assert dismiss._deferred_dismiss_source_id == live_id


def test_uninstall_leaves_no_orphan_timers() -> None:
    _closed, _bus, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None
    timers = _FakeTimers()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        with mock.patch("shell.popup_handle.GLib.timeout_add", side_effect=timers.timeout_add):
            with mock.patch("shell.popup_handle.GLib.source_remove", side_effect=timers.source_remove):
                popup.emit("leave-notify-event", object())
                timer_id = dismiss._deferred_dismiss_source_id
                dismiss.uninstall()

    assert timer_id != 0
    assert dismiss._deferred_dismiss_source_id == 0
    assert timers.active == {}
    assert timers.removed == [timer_id]


def test_config_grace_period_is_half_second() -> None:
    assert POPUP_OUTSIDE_DISMISS_GRACE_MS == 500


if __name__ == "__main__":
    test_leave_does_not_start_timer_while_pointer_inside()
    test_enter_leave_dismisses_after_grace()
    test_enter_leave_enter_before_grace_does_not_dismiss()
    test_leave_then_focus_out_still_dismisses()
    test_leave_then_focus_out_then_click_other_window_still_dismisses()
    test_leave_then_content_refresh_still_dismisses()
    test_leave_enter_leave_starts_new_timer()
    test_focus_out_does_not_cancel_or_replace_pointer_timer()
    test_stale_timer_cannot_close_reopened_popup()
    test_uninstall_leaves_no_orphan_timers()
    test_config_grace_period_is_half_second()
    print("popup outside dismiss tests OK")
