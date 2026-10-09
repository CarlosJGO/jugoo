"""An incoming notification card never grows past its design width."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from shell.config import NOTIFICATIONS_TOAST_WIDTH
from shell.models import NotificationAction, NotificationSnapshot
from shell.widgets.notificaciones.notification_toast import NotificationToast


class _Service:
    def resolve_display_timeout_ms(self, _snapshot: NotificationSnapshot) -> int:
        return 0


def _toast(snapshot: NotificationSnapshot) -> NotificationToast:
    toast = NotificationToast(
        _Service(),  # type: ignore[arg-type]
        on_invoke_action=lambda *_args: None,
        on_dismiss=lambda *_args: None,
    )
    toast.show_notification(snapshot)
    return toast


def _long_snapshot() -> NotificationSnapshot:
    return NotificationSnapshot(
        id=1,
        app_name="Aplicación con un nombre larguísimo " * 4,
        app_icon="",
        summary="Un título que no termina nunca " * 12,
        body="palabra " * 80 + "https://example.com/" + "x" * 300,
        actions=(
            NotificationAction("a", "Acción con una etiqueta muy muy larga para probar"),
            NotificationAction("b", "Otra acción con una etiqueta enorme también"),
        ),
        urgency=2,
        timestamp=0.0,
        expire_timeout_ms=0,
    )


def _pump(milliseconds: int = 120) -> None:
    done: list[bool] = []
    GLib.timeout_add(milliseconds, lambda: done.append(True) or False)
    while not done:
        Gtk.main_iteration()


def test_long_content_keeps_the_card_width() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    toast = _toast(_long_snapshot())
    assert toast.get_preferred_width() == (NOTIFICATIONS_TOAST_WIDTH, NOTIFICATIONS_TOAST_WIDTH)

    window = Gtk.OffscreenWindow()
    window.add(toast)
    window.show_all()
    _pump()
    assert window.get_allocated_width() == NOTIFICATIONS_TOAST_WIDTH
    for part in (toast._summary, toast._body, toast._actions_box, toast._card):
        allocation = part.get_allocation()
        assert allocation.x >= 0
        assert allocation.x + allocation.width <= NOTIFICATIONS_TOAST_WIDTH
    window.destroy()


def test_long_text_wraps_inside_the_card() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    toast = _toast(_long_snapshot())
    window = Gtk.OffscreenWindow()
    window.add(toast)
    window.show_all()
    _pump()
    single_line = toast._app_label.get_allocated_height()
    assert toast._summary.get_allocated_height() > single_line
    assert toast._body.get_allocated_height() > toast._summary.get_allocated_height()
    window.destroy()


def test_short_content_keeps_the_same_width() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    toast = _toast(
        NotificationSnapshot(
            id=2,
            app_name="Ana",
            app_icon="",
            summary="Hola",
            body="",
            actions=(),
            urgency=1,
            timestamp=0.0,
            expire_timeout_ms=0,
        )
    )
    assert toast.get_preferred_width() == (NOTIFICATIONS_TOAST_WIDTH, NOTIFICATIONS_TOAST_WIDTH)


if __name__ == "__main__":
    test_long_content_keeps_the_card_width()
    test_long_text_wraps_inside_the_card()
    test_short_content_keeps_the_same_width()
    print("ok")
