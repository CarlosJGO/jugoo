"""The notifications panel maps its chrome before stacking blocks one by one."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from shell.config import (
    NOTIFICATION_POPUP_MAX_HEIGHT,
    NOTIFICATION_POPUP_REVEAL_STAGGER_MS,
)
from shell.models import NotificationSnapshot
from shell.widgets.notificaciones.notification_popup import (
    _FIRST_BLOCK_DELAY_MS,
    NotificationPopup,
    stack_pane_left,
)


def _snap(nid: int, app: str) -> NotificationSnapshot:
    return NotificationSnapshot(
        id=nid,
        app_name=app,
        app_icon="",
        summary=app,
        body="",
        actions=(),
        urgency=1,
        timestamp=float(nid),
        expire_timeout_ms=5000,
    )


class _Service:
    def __init__(self, items: tuple[NotificationSnapshot, ...]) -> None:
        self.history_snapshots = items
        self.paused = False
        self.sound_muted_apps: tuple[str, ...] = ()
        self.blocked_apps: tuple[str, ...] = ()

    def app_key_for(self, snapshot: NotificationSnapshot) -> str:
        return snapshot.app_name.casefold()

    def is_app_sound_muted(self, _key: str) -> bool:
        return False

    def is_app_blocked(self, _key: str) -> bool:
        return False


def _noop(*_args: object) -> None:
    return None


def _pump(milliseconds: int) -> None:
    done: list[bool] = []
    GLib.timeout_add(milliseconds, lambda: done.append(True) or False)
    while not done:
        Gtk.main_iteration()


def _popup(items: tuple[NotificationSnapshot, ...]) -> NotificationPopup:
    return NotificationPopup(
        Gtk.Window(),
        _Service(items),  # type: ignore[arg-type]
        is_sound_enabled=lambda: True,
        on_mark_read=_noop,
        on_mark_all_read=_noop,
        on_dismiss=_noop,
        on_clear_all=_noop,
        on_invoke_action=_noop,
        on_open_app=_noop,
        on_open_group_window=_noop,
        on_mark_group_read=_noop,
        on_dismiss_group=_noop,
        on_toggle_paused=_noop,
        on_toggle_app_sound_mute=_noop,
        on_toggle_app_blocked=_noop,
    )


def test_stacked_rows_do_not_stretch_the_window() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    popup = _popup(tuple(_snap(index, "Ana") for index in range(1, 8)))
    assert popup._stack_scrolled.get_propagate_natural_height() is False
    assert popup._main.get_hexpand() is False
    assert popup._main.get_halign() == Gtk.Align.END
    parent_request = popup._main.get_size_request()
    popup.show_stacked([_snap(index, "Ana") for index in range(1, 8)])
    popup.hide_stacked()
    assert popup._main.get_size_request().width == parent_request.width
    assert popup._main.get_size_request().height == parent_request.height
    assert popup._stack_revealer.get_reveal_child() is False
    assert popup.get_size_request().width == 360
    popup._cancel_top_pin()
    popup.destroy()


def test_stack_pane_grows_left_of_the_list() -> None:
    assert stack_pane_left(1000, 360) == 640
    assert stack_pane_left(1000, 720) == 280


def test_hover_group_opens_the_left_pane() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    popup = _popup((_snap(1, "Ana"), _snap(2, "Ana"), _snap(3, "Luis")))
    popup.show_stacked([_snap(1, "Ana"), _snap(2, "Ana")])
    assert popup._stack_revealer.get_reveal_child() is True
    assert len(popup._stack_list.get_children()) == 2
    assert "Ana" in popup._stack_title.get_text()

    popup.show_stacked([_snap(3, "Luis")])
    assert popup._stack_revealer.get_reveal_child() is False

    popup.show_stacked([_snap(1, "Ana"), _snap(2, "Ana")])
    popup.hide_stacked()
    assert popup._stack_revealer.get_reveal_child() is False
    assert popup.get_size_request().width == 360
    assert popup._main.get_size_request().width == 360
    popup._cancel_top_pin()
    popup.destroy()


def test_blocks_stack_one_at_a_time_after_the_window() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    items = (_snap(1, "Correo"), _snap(2, "Reloj"), _snap(3, "Mapas"))
    popup = _popup(items)
    button = Gtk.Button()
    popup._anchor_button = button
    popup._prepare_open_shell()

    assert popup._scrolled.get_max_content_height() == NOTIFICATION_POPUP_MAX_HEIGHT
    assert popup._list_box.get_children() == []

    popup._arm_progressive_reveal()
    assert popup._list_box.get_children() == []

    _pump(_FIRST_BLOCK_DELAY_MS + 30)
    first = popup._list_box.get_children()
    assert len(first) == 1
    assert isinstance(first[0], Gtk.Revealer)
    assert first[0].get_reveal_child() is True
    assert first[0].get_transition_type() == Gtk.RevealerTransitionType.SLIDE_DOWN
    assert first[0].get_transition_duration() > 0

    _pump(NOTIFICATION_POPUP_REVEAL_STAGGER_MS + 40)
    assert len(popup._list_box.get_children()) == 2

    _pump(NOTIFICATION_POPUP_REVEAL_STAGGER_MS + 40)
    assert len(popup._list_box.get_children()) == 3

    popup._cancel_progressive_reveal()
    popup.destroy()


def test_each_taller_frame_repins_the_top() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    popup = _popup((_snap(1, "Correo"),))
    popup._anchor_button = Gtk.Button()
    popup._fixed_popup_top = 40
    pins: list[tuple[int, int]] = []
    popup._pin_top_to_bar = lambda: pins.append(popup._pinned_size)  # type: ignore[method-assign]

    rect = Gdk.Rectangle()
    rect.width = 360
    rect.height = 80
    popup._on_size_allocate_pin_top(popup, rect)
    popup._on_size_allocate_pin_top(popup, rect)
    rect.height = 140
    popup._on_size_allocate_pin_top(popup, rect)
    rect.width = 720
    popup._on_size_allocate_pin_top(popup, rect)

    assert pins == [(360, 80), (360, 140), (720, 140)]
    assert popup._pin_source_id == 0
    popup._cancel_top_pin()
    popup.destroy()


if __name__ == "__main__":
    test_stacked_rows_do_not_stretch_the_window()
    test_stack_pane_grows_left_of_the_list()
    test_hover_group_opens_the_left_pane()
    test_blocks_stack_one_at_a_time_after_the_window()
    test_each_taller_frame_repins_the_top()
    print("ok")
