"""Tests for PopupOutsideDismiss click-outside close (no focus dismiss)."""

from __future__ import annotations

from unittest import mock

import gi

gi.require_version("GLib", "2.0")

from shell.popup_handle import PopupOutsideDismiss


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

    def get_window(self):
        return None

    def get_title(self) -> str:
        return self._title

    def set_opacity(self, value: float) -> None:
        self._opacity = value

    def get_opacity(self) -> float:
        return self._opacity

    def show_all(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False


def _install_dismiss(
    dismiss: PopupOutsideDismiss,
    *,
    popup: _Connectable | None = None,
    anchor: _Connectable | None = None,
) -> tuple[list[bool], PopupOutsideDismiss]:
    closed: list[bool] = []
    bus = _FakeEventBus()
    popup = popup or _Connectable()
    shell = _Connectable(title="Shell")
    anchor = anchor or _Connectable(title="Anchor")
    dismiss.install(popup, shell, (anchor,), lambda: closed.append(True), bus)
    dismiss._install_grace_until = 0
    return closed, dismiss


def test_leave_does_not_close() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        popup.emit("leave-notify-event", object())

    assert closed == []
    assert dismiss._popup is not None


def test_shell_click_outside_closes() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        dismiss.on_shell_bar_button_press(1)

    assert closed == [True]
    assert dismiss._popup is None


def test_shell_click_on_popup_keeps_open() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=True):
        dismiss.on_shell_bar_button_press(1)

    assert closed == []
    assert dismiss._popup is not None


def test_pointer_outside_hook_closes() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        dismiss.dismiss_if_pointer_outside()

    assert closed == [True]
    assert dismiss._popup is None


def test_pointer_outside_hook_keeps_open_when_over_popup() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=True):
        dismiss.dismiss_if_pointer_outside()

    assert closed == []
    assert dismiss._popup is not None


def test_focus_out_does_not_close() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        popup.emit("focus-out-event", object())

    assert closed == []
    assert dismiss._popup is not None


def test_suspend_blocks_shell_click_dismiss() -> None:
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())
    dismiss.suspend()

    with mock.patch("shell.popup_handle.pointer_inside_widget", return_value=False):
        dismiss.on_shell_bar_button_press(1)

    assert closed == []
    assert dismiss._popup is not None


def test_pointer_over_transient_combo_keeps_open() -> None:
    """ComboBox menus are separate toplevels with transient-for the popup."""
    closed, dismiss = _install_dismiss(PopupOutsideDismiss())
    popup = dismiss._popup
    assert popup is not None

    fake_menu = mock.Mock()
    fake_menu.get_visible.return_value = True
    fake_menu.get_mapped.return_value = True
    fake_menu.get_window.return_value = object()
    fake_menu.get_transient_for.return_value = popup

    with mock.patch(
        "shell.popup_handle.pointer_inside_widget",
        side_effect=lambda widget: widget is fake_menu,
    ), mock.patch(
        "shell.popup_handle._iter_owned_transient_windows",
        return_value=[fake_menu],
    ), mock.patch(
        "shell.popup_handle._iter_registered_owned_surfaces",
        return_value=[],
    ), mock.patch(
        "shell.popup_handle.Gtk.grab_get_current",
        return_value=None,
    ):
        dismiss.dismiss_if_pointer_outside()

    assert closed == []
    assert dismiss._popup is not None


if __name__ == "__main__":
    test_leave_does_not_close()
    test_shell_click_outside_closes()
    test_shell_click_on_popup_keeps_open()
    test_pointer_outside_hook_closes()
    test_pointer_outside_hook_keeps_open_when_over_popup()
    test_focus_out_does_not_close()
    test_suspend_blocks_shell_click_dismiss()
    test_pointer_over_transient_combo_keeps_open()
    print("popup outside dismiss tests OK")
