"""6.4: popup ∪ card as one hover region, and keeping in step with the popup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from shell import config as shell_config
from shell.eventbus import EventBus
from shell.servicios.notificaciones import notifications as service_module
from shell.servicios.notificaciones.notifications import NotificationService
from shell.tests.test_notification_deck_switch import (
    MONITOR,
    PARENT,
    _UnmappedCard,
    _group,
    _open,
    _paint,
    _run,
)
from shell.widgets.notificaciones import notification_deck as deck_module

# Requests act at once here; the hover-intent wait has its own test.
shell_config.NOTIFICATIONS_DECK_HOVER_INTENT_MS = 0
from shell.widgets.notificaciones.notification_deck import NotificationDeck
from shell.widgets.notificaciones.notification_deck_motion import (
    DeckState,
    HoverRegion,
    Rect,
)


def test_region_enters_at_once_and_leaves_after_the_tolerance() -> None:
    region = HoverRegion(lambda: 120)
    assert region.sample(True, 0) is True
    assert region.sample(False, 10) is None
    assert region.sample(True, 60) is None  # brushed the edge and came back
    assert region.sample(False, 100) is None
    assert region.sample(False, 210) is None
    assert region.sample(False, 220) is False
    assert region.sample(False, 400) is None


class _Pointer:
    def __init__(self) -> None:
        self.at: tuple[int, int] | None = None

    def __call__(self) -> tuple[int, int] | None:
        return self.at


def _deck(pointer: _Pointer, hover: list[bool]) -> tuple[NotificationDeck, _UnmappedCard]:
    deck = NotificationDeck(
        Gtk.Window(),
        on_dismiss=lambda _id: None,
        on_hover_changed=hover.append,
        window_factory=_UnmappedCard,
        pointer=pointer,
    )
    return deck, deck.ensure_window()  # type: ignore[return-value]


_PATCHES: list[tuple[object, str, object]] = []


def _patch(module: object, name: str, value: object) -> None:
    _PATCHES.append((module, name, getattr(module, name)))
    setattr(module, name, value)


def _unpatch() -> None:
    while _PATCHES:
        module, name, value = _PATCHES.pop()
        setattr(module, name, value)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_pointer_crossing_from_the_parent_to_the_card_keeps_it_out() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    clock = _Clock()
    _patch(deck_module, "_now_ms", clock)
    pointer = _Pointer()
    hover: list[bool] = []
    deck, card = _deck(pointer, hover)
    _open(deck, card, _group(10, 3, "Ana"))
    assert deck.machine.state is DeckState.VISIBLE
    visible = deck.machine.card_rect()
    assert visible is not None and visible.x < PARENT.x

    # Walk left from the parent across the seam and over the card.
    for x in range(PARENT.right - 20, visible.x + 10, -20):
        pointer.at = (x, PARENT.y + 200)
        clock.now += 40
        deck._sample_pointer()
        assert deck.machine.wanted is not None, x
    assert hover == [True]
    assert deck.pointer_is_inside() is True

    # Out of both for longer than the tolerance: the card goes back.
    pointer.at = (visible.x - 50, PARENT.y + 200)
    clock.now += 40
    deck._sample_pointer()
    assert deck.machine.wanted is not None
    clock.now += shell_config.NOTIFICATIONS_DECK_HOVER_GRACE_MS + 1
    deck._sample_pointer()
    assert deck.machine.wanted is None
    assert deck.machine.state is DeckState.EXITING
    assert hover == [True, False]
    deck.close()


def test_a_drag_from_the_card_keeps_it_out() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    clock = _Clock()
    _patch(deck_module, "_now_ms", clock)
    pointer = _Pointer()
    deck, card = _deck(pointer, [])
    _open(deck, card, _group(10, 3, "Ana"))
    held = [True]
    deck._button_held_on_card = lambda: held[0]  # type: ignore[method-assign]
    pointer.at = (PARENT.x + 10, PARENT.y + 10)
    deck._sample_pointer()

    pointer.at = (0, 0)
    for _ in range(10):
        clock.now += 40
        deck._sample_pointer()
    assert deck.machine.wanted is not None

    held[0] = False
    clock.now += 40
    deck._sample_pointer()
    clock.now += shell_config.NOTIFICATIONS_DECK_HOVER_GRACE_MS + 1
    deck._sample_pointer()
    assert deck.machine.wanted is None
    deck.close()


def test_close_while_hovered_reports_the_leave() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    pointer = _Pointer()
    hover: list[bool] = []
    deck, card = _deck(pointer, hover)
    _open(deck, card, _group(10, 3, "Ana"))
    pointer.at = (PARENT.x + 10, PARENT.y + 10)
    deck._sample_pointer()
    deck.close()
    assert hover == [True, False]
    assert deck._watch_id == 0
    assert card.get_mapped() is False
    assert deck.machine.state is DeckState.HIDDEN and deck.machine.shown is None


def test_parent_resize_while_out_returns_on_the_new_track() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck(_Pointer(), [])
    now = _open(deck, card, _group(10, 3, "Ana"))
    smaller = Rect(PARENT.x, PARENT.y, PARENT.width, PARENT.height - 100)
    deck.set_parent(smaller, MONITOR)
    assert deck.machine.state is DeckState.EXITING
    assert deck.machine.wanted[1].height == smaller.height - 2 * shell_config.NOTIFICATION_DECK_INSET
    _run(deck, now, now + 2000)
    # Back home it is remapped at the new size before coming out again.
    assert card.card_size == (deck.machine.wanted[1].width, deck.machine.wanted[1].height)
    _paint(deck, card)
    assert deck.machine.state is DeckState.ENTERING
    deck.close()


def test_resting_card_that_no_longer_fits_is_unmapped() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck(_Pointer(), [])
    now = _open(deck, card, _group(10, 3, "Ana"))
    deck.release()
    _run(deck, now, now + 2000)
    assert deck.machine.state is DeckState.HIDDEN and card.get_mapped()

    deck.set_parent(Rect(PARENT.x, PARENT.y, PARENT.width, PARENT.height - 100), MONITOR)
    assert card.get_mapped() is False
    assert deck.machine.shown is None

    # The next request loads afresh and comes out like the first time.
    deck.show_group(_group(10, 3, "Ana"), deck._parent, MONITOR)
    assert card.get_mapped() is True
    _paint(deck, card)
    assert deck.machine.state is DeckState.ENTERING
    deck.close()


def test_closed_from_outside_starts_over() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck(_Pointer(), [])
    _open(deck, card, _group(10, 3, "Ana"))
    assert deck._on_closed_outside(card, None) is True
    assert card.get_mapped() is False
    assert deck.machine.state is DeckState.HIDDEN and deck.machine.shown is None

    deck.show_group(_group(20, 2, "Luis"), PARENT, MONITOR)
    _paint(deck, card)
    assert deck.machine.state is DeckState.ENTERING
    deck.close()


def test_expirations_hold_with_the_time_left_and_resume() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        service = NotificationService(EventBus(), history_path=Path(tmpdir) / "n.json")
        clock = _Clock()
        _patch(service_module, "_monotonic_ms", clock)
        snapshot = service._store.add(
            app_name="App",
            replaces_id=0,
            app_icon="",
            icon_name="",
            image_path="",
            summary="s",
            body="",
            actions=(),
            urgency=1,
            expire_timeout_ms=150,
        )
        service._schedule_expiration(snapshot)
        clock.now = 100
        service.hold_expirations()
        assert service._expire_timers == {}
        assert service._expire_held == {snapshot.id: 50}

        # Arrives while held: its clock starts on resume.
        late = service._store.add(
            app_name="App",
            replaces_id=0,
            app_icon="",
            icon_name="",
            image_path="",
            summary="t",
            body="",
            actions=(),
            urgency=1,
            expire_timeout_ms=5000,
        )
        service._schedule_expiration(late)
        assert service._expire_timers == {}

        context = GLib.MainContext.default()
        deadline = GLib.get_monotonic_time() + 200_000
        while GLib.get_monotonic_time() < deadline:
            context.iteration(False)
        assert service._store.get(snapshot.id).expired is False

        service.resume_expirations()
        assert set(service._expire_timers) == {snapshot.id, late.id}
        deadline = GLib.get_monotonic_time() + 300_000
        while GLib.get_monotonic_time() < deadline and not service._store.get(snapshot.id).expired:
            context.iteration(False)
        assert service._store.get(snapshot.id).expired is True
        assert service._store.get(late.id).expired is False
        service.close()



def test_enter_without_the_pointer_on_the_popup_is_ignored() -> None:
    """GTK replays an enter on the row left under a stale pointer position."""
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    pointer = _Pointer()
    deck, card = _deck(pointer, [])
    pointer.at = (PARENT.x - 300, PARENT.y + 50)
    deck.show_group(_group(10, 3, "Ana"), PARENT, MONITOR)
    assert deck.machine.wanted is None
    assert card._prepared is None

    pointer.at = (PARENT.x + 20, PARENT.y + 50)
    deck.show_group(_group(10, 3, "Ana"), PARENT, MONITOR)
    assert deck.machine.wanted is not None
    deck.close()


if __name__ == "__main__":
    for test in (
        test_region_enters_at_once_and_leaves_after_the_tolerance,
        test_pointer_crossing_from_the_parent_to_the_card_keeps_it_out,
        test_a_drag_from_the_card_keeps_it_out,
        test_close_while_hovered_reports_the_leave,
        test_parent_resize_while_out_returns_on_the_new_track,
        test_resting_card_that_no_longer_fits_is_unmapped,
        test_closed_from_outside_starts_over,
        test_expirations_hold_with_the_time_left_and_resume,
        test_enter_without_the_pointer_on_the_popup_is_ignored,
    ):
        try:
            test()
        finally:
            _unpatch()
    print("ok")
