"""Changing parent: the visible group stays untouched until the card is hidden."""

from __future__ import annotations

from dataclasses import replace

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from shell import config as shell_config
from shell.models import NotificationSnapshot
from shell.widgets.notificaciones import notification_deck as deck_module

# Requests act at once here; the hover-intent wait has its own test.
shell_config.NOTIFICATIONS_DECK_HOVER_INTENT_MS = 0
from shell.widgets.notificaciones.notification_deck import (
    NotificationDeck,
    NotificationDeckWindow,
)
from shell.widgets.notificaciones.notification_deck_motion import DeckState, Rect

MOVES: list[int] = []
deck_module.move_window_under = lambda _title, x, _y, *, above_title, animate=False: MOVES.append(x)
# The deck stamps motions with this clock; the tests move it by hand.
CLOCK = [0.0]
deck_module._now_ms = lambda: CLOCK[0]
deck_module.force_window_opaque = lambda _title: None

PARENT = Rect(1100, 200, 360, 420)
MONITOR = Rect(0, 0, 1920, 1080)


class _UnmappedCard(NotificationDeckWindow):
    """Real widget tree; mapping and frames are driven by the test."""

    def __init__(self, shell_window: Gtk.Window) -> None:
        super().__init__(shell_window)
        self.fake_mapped = False
        self.fills: list[str] = []

    def show_all(self) -> None:
        self.fake_mapped = True

    def hide(self) -> None:
        self.fake_mapped = False

    def get_mapped(self) -> bool:
        return self.fake_mapped

    def add_tick_callback(self, _callback) -> int:
        return 1

    def remove_tick_callback(self, _id: int) -> None:
        return None

    def fill(self) -> None:
        super().fill()
        self.fills.append(self.title_text)

    @property
    def title_text(self) -> str:
        return self._title.get_text()

    @property
    def row_count(self) -> int:
        return len(self._list.get_children())


def _group(base: int, size: int, who: str) -> list[NotificationSnapshot]:
    return [
        NotificationSnapshot(
            id=base + index,
            app_name=who,
            app_icon="",
            summary=who,
            body="hola",
            actions=(),
            urgency=1,
            timestamp=float(base + index),
            expire_timeout_ms=0,
        )
        for index in range(size)
    ]


def _deck() -> tuple[NotificationDeck, _UnmappedCard]:
    deck = NotificationDeck(
        Gtk.Window(),
        on_dismiss=lambda _id: None,
        window_factory=_UnmappedCard,
        pointer=lambda: None,
    )
    return deck, deck.ensure_window()  # type: ignore[return-value]


def _paint(deck: NotificationDeck, card: _UnmappedCard) -> None:
    """Rows built, then the frame after a load: unveil if just mapped, report."""
    _idle()
    if card.veiled:
        deck._unveil_under_parent()
    deck._on_painted(card, None)
    _idle()


def _idle() -> None:
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def _tick(deck: NotificationDeck, now: float) -> bool:
    CLOCK[0] = now
    return deck.machine.tick(now)


def _run(deck: NotificationDeck, start: float, until: float) -> float:
    now = start
    while now <= until and _tick(deck, now):
        now += 10
    return now


def _open(deck: NotificationDeck, card: _UnmappedCard, group) -> float:
    deck.show_group(group, PARENT, MONITOR)
    _paint(deck, card)
    return _run(deck, CLOCK[0], CLOCK[0] + 2000)


def test_new_parent_keeps_the_visible_group_until_hidden() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck()
    now = _open(deck, card, _group(10, 3, "Ana"))
    assert deck.machine.state is DeckState.VISIBLE
    assert (card.title_text, card.row_count) == ("Ana  ·  3", 3)

    deck.show_group(_group(20, 4, "Luis"), PARENT, MONITOR)
    assert deck.machine.state is DeckState.EXITING
    assert card._prepared is not None and card._prepared.ids == (20, 21, 22, 23)
    _idle()
    assert card.is_prepared((20, 21, 22, 23))

    hidden_x = deck.machine.track.hidden_x
    while deck.machine.state is DeckState.EXITING:
        assert (card.title_text, card.row_count) == ("Ana  ·  3", 3)
        _tick(deck, now)
        now += 10
    assert MOVES[-1] == hidden_x
    assert (card.title_text, card.row_count) == ("Luis  ·  4", 4)
    assert deck.machine.state is DeckState.HIDDEN

    _paint(deck, card)
    assert deck.machine.state is DeckState.ENTERING
    assert card.fills == ["Ana  ·  3", "Luis  ·  4"]
    deck.close()


def test_rapid_parent_changes_load_only_the_last_one() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck()
    now = _open(deck, card, _group(10, 3, "Ana"))
    for base, who in ((20, "Luis"), (30, "Eva"), (40, "Sol")):
        deck.show_group(_group(base, 2, who), PARENT, MONITOR)
    _run(deck, now, now + 2000)
    _paint(deck, card)
    assert card.fills == ["Ana  ·  3", "Sol  ·  2"]
    assert deck.machine.state is DeckState.ENTERING
    deck.close()


def test_coming_back_mid_exit_keeps_the_content() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck()
    ana = _group(10, 3, "Ana")
    now = _open(deck, card, ana)
    deck.show_group(_group(20, 4, "Luis"), PARENT, MONITOR)
    _tick(deck, now)
    _tick(deck, now + 60)
    deck.show_group(ana, PARENT, MONITOR)
    assert deck.machine.state is DeckState.ENTERING
    _run(deck, now + 60, now + 3000)
    assert deck.machine.state is DeckState.VISIBLE
    assert card.fills == ["Ana  ·  3"]
    deck.close()


def test_a_large_group_is_built_in_slices_before_the_card_comes_out() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck()
    deck.show_group(_group(100, 60, "Mucho"), PARENT, MONITOR)
    assert card.get_mapped() is False
    assert deck.machine.state is DeckState.HIDDEN

    context = GLib.MainContext.default()
    turns = 0
    while not card.get_mapped():
        started = GLib.get_monotonic_time()
        assert context.iteration(False)
        # Each turn stays near the budget; a whole group at once took ~80 ms.
        assert GLib.get_monotonic_time() - started < 30_000
        turns += 1
    assert turns > 1
    _paint(deck, card)
    # Only the newest rows get built; the rest is summed up at the end.
    limit = shell_config.NOTIFICATIONS_DECK_MAX_ROWS
    children = card._list.get_children()
    assert len(children) == limit + 1
    assert children[-1].get_label() == f"Ver todas ({60 - limit} más)"
    assert card.title_text == "Mucho  ·  60"
    assert deck.machine.state is DeckState.ENTERING

    # "Ver todas" appends the rest below, in slices, and the button goes away.
    children[-1].clicked()
    assert card.row_count == limit
    while card._rest_id:
        started = GLib.get_monotonic_time()
        context.iteration(False)
        assert GLib.get_monotonic_time() - started < 30_000
    rows = card._list.get_children()
    assert len(rows) == 60
    assert [row._snapshot.id for row in rows] == list(range(100, 160))
    deck.close()


def test_sweeping_across_parents_only_acts_on_where_the_pointer_rests() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    previous = shell_config.NOTIFICATIONS_DECK_HOVER_INTENT_MS
    shell_config.NOTIFICATIONS_DECK_HOVER_INTENT_MS = 60
    try:
        deck, card = _deck()
        for base, who in ((20, "Luis"), (30, "Eva"), (40, "Sol")):
            deck.show_group(_group(base, 2, who), PARENT, MONITOR)
            deck.show_group(_group(50, 1, "Solo"), PARENT, MONITOR)
        deck.show_group(_group(60, 3, "Ana"), PARENT, MONITOR)
        assert deck.machine.wanted is None
        assert card._prepared is None

        context = GLib.MainContext.default()
        deadline = GLib.get_monotonic_time() + 400_000
        while deck.machine.wanted is None and GLib.get_monotonic_time() < deadline:
            context.iteration(False)
        assert deck.machine.wanted is not None and deck.machine.wanted[0] == (60, 61, 62)
        _paint(deck, card)
        assert card.fills == ["Ana  ·  3"]

        # Back on the same parent: nothing to wait for.
        deck.show_group(_group(60, 3, "Ana"), PARENT, MONITOR)
        assert deck._intent_id == 0
        deck.close()
    finally:
        shell_config.NOTIFICATIONS_DECK_HOVER_INTENT_MS = previous


def test_left_click_on_a_card_row_activates_it() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    activated: list[int] = []
    deck = NotificationDeck(
        Gtk.Window(),
        on_dismiss=lambda _id: None,
        on_activate=lambda snapshot: activated.append(snapshot.id),
        window_factory=_UnmappedCard,
        pointer=lambda: None,
    )
    card = deck.ensure_window()
    group = [replace(item, desktop_entry="org.example.App") for item in _group(10, 2, "Ana")]
    _open(deck, card, group)
    row = card._list.get_children()[0]
    for button, expected in ((3, []), (1, [10])):
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.button.button = button
        row.emit("button-press-event", event)
        assert activated == expected
    deck.close()


def test_parent_without_group_hides_without_loading() -> None:
    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    deck, card = _deck()
    now = _open(deck, card, _group(10, 3, "Ana"))
    deck.show_group(_group(50, 1, "Solo"), PARENT, MONITOR)
    assert deck.machine.state is DeckState.EXITING
    _run(deck, now, now + 2000)
    assert deck.machine.state is DeckState.HIDDEN
    assert card.fills == ["Ana  ·  3"]
    assert card._prepared is None
    deck.close()


if __name__ == "__main__":
    test_new_parent_keeps_the_visible_group_until_hidden()
    test_rapid_parent_changes_load_only_the_last_one()
    test_coming_back_mid_exit_keeps_the_content()
    test_a_large_group_is_built_in_slices_before_the_card_comes_out()
    test_sweeping_across_parents_only_acts_on_where_the_pointer_rests()
    test_left_click_on_a_card_row_activates_it()
    test_parent_without_group_hides_without_loading()
    print("ok")
