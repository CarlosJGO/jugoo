"""The popup hands groups to the card and never resizes."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from shell.tests.test_notification_popup_reveal import _Service, _noop, _snap
from shell.widgets.notificaciones import notification_popup as popup_module
from shell.widgets.notificaciones.notification_deck_motion import Rect
from shell.widgets.notificaciones.notification_popup import NotificationPopup

popup_module.force_window_opaque = lambda _title: None
popup_module.monitor_containing_point = lambda _x, _y: None


class _FakeDeck:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.wanted: tuple[int, ...] = ()

    def show_group(self, group, parent, monitor) -> None:
        self.calls.append(("show", tuple(s.id for s in group), parent, monitor))

    def release(self) -> None:
        self.calls.append(("release",))

    def close(self) -> None:
        self.calls.append(("close",))

    def set_parent(self, parent, monitor) -> None:
        self.calls.append(("parent", parent))

    def wanted_ids(self) -> tuple[int, ...]:
        return self.wanted


def _popup(deck: _FakeDeck, service: _Service | None = None) -> NotificationPopup:
    popup = NotificationPopup(
        Gtk.Window(),
        service or _Service((_snap(1, "Ana"), _snap(2, "Ana"))),  # type: ignore[arg-type]
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
        deck=deck,  # type: ignore[arg-type]
    )
    popup._position = (1500, 40, 360)
    popup._deck_parent = Rect(1500, 40, 360, 420)
    return popup


def test_group_goes_to_the_card_and_the_popup_keeps_its_width() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck = _FakeDeck()
    popup = _popup(deck)
    popup.show_stacked([_snap(1, "Ana"), _snap(2, "Ana")])

    kind, ids, parent, _monitor = deck.calls[-1]
    assert (kind, ids) == ("show", (1, 2))
    assert parent == Rect(1500, 40, 360, 420)
    assert popup.get_size_request().width == 360
    popup.destroy()


def test_hiding_releases_the_card_at_once() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck = _FakeDeck()
    popup = _popup(deck)
    popup.hide_stacked()
    assert deck.calls == [("release",)]
    popup.destroy()


def test_refresh_keeps_following_the_group_on_the_card() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    service = _Service((_snap(1, "Ana"), _snap(2, "Ana"), _snap(3, "Ana"), _snap(9, "Luis")))
    deck = _FakeDeck()
    popup = _popup(deck, service)
    deck.wanted = (1, 2, 3)

    # One of the group goes away (dismissed from the card): the rest stays on it.
    service.history_snapshots = (_snap(1, "Ana"), _snap(3, "Ana"), _snap(9, "Luis"))
    popup.refresh()
    shows = [set(call[1]) for call in deck.calls if call[0] == "show"]
    assert shows == [{1, 3}]
    assert ("release",) not in deck.calls

    # Only one left: the card goes back.
    deck.calls.clear()
    deck.wanted = (1, 3)
    service.history_snapshots = (_snap(3, "Ana"), _snap(9, "Luis"))
    popup.refresh()
    assert deck.calls == [("release",)]
    popup.destroy()


def test_unmapping_the_popup_closes_the_card() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck = _FakeDeck()
    popup = _popup(deck)
    popup.show_all()
    popup.hide()
    assert ("close",) in deck.calls
    popup.destroy()


if __name__ == "__main__":
    test_group_goes_to_the_card_and_the_popup_keeps_its_width()
    test_hiding_releases_the_card_at_once()
    test_refresh_keeps_following_the_group_on_the_card()
    test_unmapping_the_popup_closes_the_card()
    print("ok")
