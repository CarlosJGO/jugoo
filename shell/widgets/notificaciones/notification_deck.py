"""Grouped notifications as a card that slides out from behind the popup.

A persistent floating window kept right under the notifications popup. The
popup never moves or resizes for it: the card rests on a hidden rect inside
the popup (which is painted opaque) and Hyprland slides it sideways with its
``windowsMove`` animation. See ``notification_deck_motion`` for the rules.
"""

from __future__ import annotations

import math
from typing import Callable, Hashable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ... import config as shell_config
from ...config import NOTIFICATION_POPUP_LIST_SPACING
from ...identity import TITLE_NOTIFICATION_DECK, TITLE_NOTIFICATIONS
from ...models import NotificationSnapshot
from ...ui.disfraces import WindowRole, dress_window
from ...ui.theme import active_theme
from ...window_identity import (
    configure_toplevel,
    force_window_opaque,
    hyprland_cursor_position,
    move_window_under,
    register_shell_popup,
)
from .notification_deck_motion import (
    DeckMachine,
    DeckMotion,
    DeckState,
    DeckTrack,
    HoverRegion,
    Rect,
    deck_track,
)
from .notification_mini_row import NotificationMiniRow


# Per main-loop turn spent building rows; well under one 100 Hz frame.
_BUILD_BUDGET_US = 4_000


class _PreparedGroup:
    def __init__(
        self,
        ids: tuple[int, ...],
        title: str,
        pending: list[NotificationSnapshot],
        make_row: Callable[[NotificationSnapshot], Gtk.Widget],
        rest: list[NotificationSnapshot],
    ) -> None:
        self.ids = ids
        self.title = title
        self.pending = pending
        self.make_row = make_row
        # Built only on "Ver todas", once the card shows the group.
        self.rest = rest
        self.rows: list[Gtk.Widget] = []
        self.on_ready: list[Callable[[], None]] = []


class NotificationDeckWindow(Gtk.Window):
    """The card itself: a title and the stacked rows, at a size it imposes.

    While veiled it paints nothing and takes no input, so it can be mapped
    anywhere and only shows once it sits under the popup.
    """

    def __init__(self, shell_window: Gtk.Window) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._size = (1, 1)
        self._veiled = True
        self._prepared: _PreparedGroup | None = None
        self._preparing_id = 0
        self._rest: tuple[list[NotificationSnapshot], Callable[[NotificationSnapshot], Gtk.Widget]] | None = None
        self._rest_id = 0
        self._more: Gtk.Button | None = None
        self.set_name("shell-notification-deck")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATION_DECK)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        # Hovering it must keep the pointer (scrolling) here; only mapping it
        # never takes focus.
        self.set_accept_focus(True)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_app_paintable(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.FOCUS_CHANGE_MASK)
        screen = self.get_screen()
        visual = screen.get_rgba_visual() if screen is not None else None
        if visual is not None:
            self.set_visual(visual)

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pane.get_style_context().add_class("notification-deck-pane")
        dress_window(self, WindowRole.NOTIFICATION_GROUP, pane)

        # Holds the visible content clear of the strip that stays under the popup.
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._content.set_vexpand(True)
        pane.pack_start(self._content, True, True, 0)

        self._title = Gtk.Label(xalign=0)
        self._title.get_style_context().add_class("notification-stack-title")
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        self._content.pack_start(self._title, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(False)
        scrolled.set_vexpand(True)
        scrolled.set_overlay_scrolling(False)
        scrolled.get_style_context().add_class("notification-popup-scroll")
        scrolled.get_style_context().add_class("notification-deck-scroll")
        self._content.pack_start(scrolled, True, True, 0)
        self._scrolled = scrolled

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=NOTIFICATION_POPUP_LIST_SPACING)
        self._list.get_style_context().add_class("notification-group-window-list")
        scrolled.add(self._list)

        self.connect("draw", self._on_draw)
        self.connect("realize", lambda *_args: self._sync_input())

    @property
    def card_size(self) -> tuple[int, int]:
        return self._size

    def set_card_size(self, width: int, height: int) -> None:
        self._size = (max(1, int(width)), max(1, int(height)))
        self.set_size_request(*self._size)
        self.resize(*self._size)
        self.queue_resize()

    def set_tucked_edge(self, *, right: bool, width: int) -> None:
        """Keep ``width`` px on the side that stays under the popup empty."""
        self._content.set_margin_end(width if right else 0)
        self._content.set_margin_start(0 if right else width)

    def do_get_preferred_width(self) -> tuple[int, int]:
        return self._size[0], self._size[0]

    def do_get_preferred_height(self) -> tuple[int, int]:
        return self._size[1], self._size[1]

    def prepare(
        self,
        group: list[NotificationSnapshot],
        make_row: Callable[[NotificationSnapshot], Gtk.Widget],
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Build the next group's rows off-window; what is on screen stays as is.

        Rows are built a few at a time when the main loop is idle, so a large
        group never holds up the card's frames. ``on_ready`` runs once all of
        them exist. Only the newest ``NOTIFICATIONS_DECK_MAX_ROWS`` get a row.
        """
        ids = _group_ids(group)
        prepared = self._prepared
        if prepared is None or prepared.ids != ids:
            self._cancel_preparing()
            head = group[0]
            title = f"{head.summary or head.app_name}  ·  {len(group)}"
            limit = max(1, int(shell_config.NOTIFICATIONS_DECK_MAX_ROWS))
            prepared = _PreparedGroup(
                ids, title, list(group[:limit]), make_row, list(group[limit:])
            )
            self._prepared = prepared
        if on_ready is not None:
            prepared.on_ready.append(on_ready)
        if prepared.pending:
            if not self._preparing_id:
                self._preparing_id = GLib.idle_add(self._build_some_rows)
        else:
            self._flush_ready(prepared)

    def is_prepared(self, ids: tuple[int, ...]) -> bool:
        prepared = self._prepared
        return prepared is not None and prepared.ids == ids and not prepared.pending

    def _build_some_rows(self) -> bool:
        prepared = self._prepared
        if prepared is None:
            self._preparing_id = 0
            return False
        deadline = GLib.get_monotonic_time() + _BUILD_BUDGET_US
        while prepared.pending and GLib.get_monotonic_time() < deadline:
            snapshot = prepared.pending.pop(0)
            row = prepared.make_row(snapshot)
            row.show_all()
            prepared.rows.append(row)
        if prepared.pending:
            return True
        self._preparing_id = 0
        self._flush_ready(prepared)
        return False

    def _flush_ready(self, prepared: "_PreparedGroup") -> None:
        callbacks, prepared.on_ready = prepared.on_ready, []
        for callback in callbacks:
            callback()

    def _cancel_preparing(self) -> None:
        if self._preparing_id:
            GLib.source_remove(self._preparing_id)
            self._preparing_id = 0

    def fill(self) -> None:
        """Swap in the prepared group in one go. Only called while the card is hidden."""
        prepared = self._prepared
        assert prepared is not None and not prepared.pending
        rows, title = prepared.rows, prepared.title
        self._prepared = None
        self._cancel_rest()
        for child in list(self._list.get_children()):
            self._list.remove(child)
        self._title.set_text(title)
        for row in rows:
            self._list.pack_start(row, False, False, 0)
        if prepared.rest:
            self._rest = (prepared.rest, prepared.make_row)
            more = Gtk.Button(label=f"Ver todas ({len(prepared.rest)} más)", relief=Gtk.ReliefStyle.NONE)
            more.get_style_context().add_class("notification-deck-more")
            more.connect("clicked", self._on_show_rest)
            self._list.pack_start(more, False, False, 0)
            self._more = more
        self._list.show_all()
        self._scrolled.get_vadjustment().set_value(0)

    def _on_show_rest(self, button: Gtk.Button) -> None:
        """The user asked for the whole group: append the rest below, in
        slices, under what is already on screen."""
        self._list.remove(button)
        self._more = None
        if self._rest is not None and not self._rest_id:
            self._rest_id = GLib.idle_add(self._append_some_rest)

    def _append_some_rest(self) -> bool:
        if self._rest is None:
            self._rest_id = 0
            return False
        pending, make_row = self._rest
        deadline = GLib.get_monotonic_time() + _BUILD_BUDGET_US
        while pending and GLib.get_monotonic_time() < deadline:
            row = make_row(pending.pop(0))
            row.show_all()
            self._list.pack_start(row, False, False, 0)
        if pending:
            return True
        self._rest = None
        self._rest_id = 0
        return False

    def _cancel_rest(self) -> None:
        if self._rest_id:
            GLib.source_remove(self._rest_id)
            self._rest_id = 0
        self._rest = None
        self._more = None

    @property
    def veiled(self) -> bool:
        return self._veiled

    def set_veiled(self, veiled: bool) -> None:
        self._veiled = veiled
        self._sync_input()
        self.queue_draw()

    def _sync_input(self) -> None:
        if self.get_window() is None:
            return
        self.input_shape_combine_region(cairo.Region() if self._veiled else None)

    def _on_draw(self, _widget: Gtk.Widget, cr) -> bool:
        if not self._veiled:
            return False
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        return True


class NotificationDeck:
    """Shows one group at a time in the card, driven by pointer requests.

    ``show_group`` / ``release`` come from the popup rows. Only the latest
    request counts; content changes while the card rests hidden, never while
    it is out.

    While the popup is open the deck samples the pointer against the popup ∪
    the card (``HoverRegion``): leaving that region sends the card back, and
    ``on_hover_changed`` reports entering and leaving it.
    """

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_dismiss: Callable[[int], None],
        on_activate: Callable[[NotificationSnapshot], None] | None = None,
        on_hover_changed: Callable[[bool], None] | None = None,
        window_factory: Callable[[Gtk.Window], NotificationDeckWindow] = NotificationDeckWindow,
        pointer: Callable[[], tuple[int, int] | None] = hyprland_cursor_position,
    ) -> None:
        self._shell_window = shell_window
        self._on_dismiss = on_dismiss
        self._on_activate = on_activate
        self._on_hover_changed = on_hover_changed
        self._window_factory = window_factory
        self._pointer = pointer
        self._window: NotificationDeckWindow | None = None
        self._groups: dict[Hashable, list[NotificationSnapshot]] = {}
        self._content_ids: tuple[int, ...] = ()
        self._awaiting_paint: Hashable | None = None
        self._awaiting_rows: Hashable | None = None
        self._tick_id = 0
        self._watch_id = 0
        self._intent_id = 0
        self._pending: list[NotificationSnapshot] | None = None
        self._parent: Rect | None = None
        self._monitor: Rect | None = None
        self._pointer_on_card = False
        self.region = HoverRegion(lambda: int(shell_config.NOTIFICATIONS_DECK_HOVER_GRACE_MS))
        self.machine = DeckMachine(
            load=self._load,
            track_for=lambda key: key[1],
            move=self._move,
            request_ticks=self._ensure_ticks,
            motion=deck_motion,
        )

    # -- requests ------------------------------------------------------------

    def show_group(
        self,
        group: list[NotificationSnapshot],
        parent: Rect,
        monitor: Rect | None,
    ) -> None:
        self.set_parent(parent, monitor)
        # A popup hidden with the pointer on it keeps that position in GTK;
        # reopened, the row laid out there gets an enter the pointer never made.
        position = self._pointer()
        if position is not None and not parent.contains(*position):
            return
        self._request(group if len(group) > 1 else None)

    def release(self) -> None:
        """Send the card back."""
        self._request(None)

    def _request(self, group: list[NotificationSnapshot] | None) -> None:
        """Hover requests only count once the pointer rests on a parent for
        ``NOTIFICATIONS_DECK_HOVER_INTENT_MS``; sweeping across parents never
        starts a motion or builds rows for each one.
        """
        ids = _group_ids(group) if group is not None else None
        wanted = self.machine.wanted
        if ids == (None if wanted is None else wanted[0]):
            self._cancel_intent()
            if group is not None:
                self._apply(group)
            return
        self._pending = group
        delay = int(shell_config.NOTIFICATIONS_DECK_HOVER_INTENT_MS)
        if delay <= 0:
            self._apply_pending()
            return
        self._cancel_intent(keep_pending=True)
        self._intent_id = GLib.timeout_add(delay, self._apply_pending)

    def _apply_pending(self) -> bool:
        self._intent_id = 0
        group, self._pending = self._pending, None
        self._apply(group)
        return False

    def _apply(self, group: list[NotificationSnapshot] | None) -> None:
        if group is None or self._parent is None:
            self.machine.want(None, _now_ms())
            self._fit_resting_card()
            return
        key = (_group_ids(group), self._track())
        self._groups = {key: group}
        if key[0] != self._content_ids:
            # The card may still be out with the current group: build the next
            # rows now and swap them in once it rests hidden.
            self.ensure_window().prepare(group, self._make_row)
        self.machine.want(key, _now_ms())

    def _cancel_intent(self, *, keep_pending: bool = False) -> None:
        if self._intent_id:
            GLib.source_remove(self._intent_id)
            self._intent_id = 0
        if not keep_pending:
            self._pending = None

    def set_parent(self, parent: Rect, monitor: Rect | None) -> None:
        """Where the popup is now; starts watching the pointer around it.

        Height changes follow immediately. Other geometry changes bring an
        exposed card back before loading its new track; a resting card that
        no longer fits is unmapped so no edge can show.
        """
        changed = (parent, monitor) != (self._parent, self._monitor)
        self._parent = parent
        self._monitor = monitor
        self._start_watch()
        if not changed:
            return
        if self._resize_height_in_place():
            return
        wanted = self.machine.wanted
        if wanted is not None and wanted[1] != self._track():
            group = self._groups.get(wanted)
            if group is not None:
                self._apply(group)
        self._fit_resting_card()

    def _resize_height_in_place(self) -> bool:
        """Follow the popup's height without restarting a horizontal slide."""
        machine = self.machine
        old = machine.track
        window = self._window
        if old is None or window is None or machine.loading is not None:
            return False
        track = self._track()
        if (old.hidden_x, old.visible_x, old.y, old.width) != (
            track.hidden_x, track.visible_x, track.y, track.width
        ):
            return False
        if old.height == track.height:
            return False

        def resized(key):
            return None if key is None else (key[0], track)

        # Content identity stays the same. Update both wishes and the shown
        # key so the machine does not treat a height change as a new group.
        self._groups = {resized(key): group for key, group in self._groups.items()}
        machine.shown = resized(machine.shown)
        machine.wanted = resized(machine.wanted)
        machine.track = track
        self._awaiting_paint = resized(self._awaiting_paint)
        window.set_card_size(track.width, track.height)
        return True

    def wanted_ids(self) -> tuple[int, ...]:
        wanted = self.machine.wanted
        return () if wanted is None else wanted[0]

    def close(self) -> None:
        """Popup closing: the card leaves at once, without any motion."""
        self._cancel_intent()
        self._stop_watch()
        self._stop_ticks()
        self._awaiting_paint = None
        self._awaiting_rows = None
        self._pointer_on_card = False
        self.machine.reset()
        if self._window is not None:
            self._window.hide()
        if self.region.inside:
            self.region.reset()
            self._report_hover(False)
        self._parent = None
        self._monitor = None

    def pointer_is_inside(self) -> bool:
        """Whether the last pointer sample fell on the card."""
        return self._pointer_on_card

    @property
    def window(self) -> NotificationDeckWindow | None:
        return self._window

    # -- loading (only while hidden) ----------------------------------------

    def _load(self, key: Hashable) -> None:
        group = self._groups.get(key)
        if group is None:
            return
        track: DeckTrack = key[1]
        window = self.ensure_window()
        ids = key[0]
        if ids != self._content_ids:
            if not window.is_prepared(ids):
                if self._awaiting_rows != key:
                    self._awaiting_rows = key
                    window.prepare(group, self._make_row, on_ready=lambda: self._resume_load(key))
                return
            window.fill()
            self._content_ids = ids
        window.set_tucked_edge(
            right=track.visible_x < track.hidden_x,
            width=max(0, int(shell_config.NOTIFICATION_DECK_OVERLAP)),
        )
        self._awaiting_paint = key
        if window.get_mapped() and window.card_size == (track.width, track.height):
            self._sink(track)
            window.queue_draw()
            return
        # A mapped window would be stretched for one frame on resize; remap it
        # veiled at the new size instead.
        window.hide()
        window.set_card_size(track.width, track.height)
        window.set_veiled(True)
        window.show_all()

    def _make_row(self, snapshot: NotificationSnapshot) -> Gtk.Widget:
        return NotificationMiniRow(
            snapshot, on_dismiss=self._on_dismiss, on_activate=self._on_activate
        )

    def _resume_load(self, key: Hashable) -> None:
        if self._awaiting_rows != key:
            return
        self._awaiting_rows = None
        if self.machine.loading == key and self.machine.state is DeckState.HIDDEN:
            self._load(key)

    def ensure_window(self) -> NotificationDeckWindow:
        """The card window, created unmapped on first use."""
        if self._window is None:
            window = self._window_factory(self._shell_window)
            window.connect("map-event", self._on_map)
            window.connect_after("draw", self._on_painted)
            window.connect("delete-event", self._on_closed_outside)
            # Focusing or clicking a floating window may raise it over the popup.
            window.connect("focus-in-event", self._restack)
            window.connect("button-press-event", self._restack)
            self._window = window
        return self._window

    def _on_map(self, window: NotificationDeckWindow, _event) -> bool:
        GLib.idle_add(self._unveil_under_parent)
        return False

    def _unveil_under_parent(self) -> bool:
        window = self._window
        key = self._awaiting_paint
        if window is None or key is None or not window.get_mapped():
            return False
        force_window_opaque(TITLE_NOTIFICATIONS)
        self._sink(key[1])
        window.set_veiled(False)
        return False

    def _sink(self, track: DeckTrack) -> None:
        move_window_under(
            TITLE_NOTIFICATION_DECK, track.hidden_x, track.y, above_title=TITLE_NOTIFICATIONS
        )

    def _on_painted(self, window: NotificationDeckWindow, _cr) -> bool:
        key = self._awaiting_paint
        if key is not None and not window.veiled:
            self._awaiting_paint = None
            # After this frame is committed, not inside its paint.
            GLib.idle_add(self._report_loaded, key)
        return False

    def _report_loaded(self, key: Hashable) -> bool:
        self.machine.loaded(key, _now_ms())
        return False

    # -- motion --------------------------------------------------------------

    def _move(self, x: int, animate: bool) -> None:
        track = self.machine.track
        if track is None:
            return
        move_window_under(
            TITLE_NOTIFICATION_DECK,
            x,
            track.y,
            above_title=TITLE_NOTIFICATIONS,
            animate=animate,
        )

    def _ensure_ticks(self) -> None:
        if self._tick_id:
            return
        remaining = self.machine.remaining_ms(_now_ms())
        self._tick_id = GLib.timeout_add(max(1, math.ceil(remaining)), self._on_tick)

    def _on_tick(self) -> bool:
        self._tick_id = 0
        if self.machine.tick(_now_ms()):
            self._ensure_ticks()
            return False
        self._fit_resting_card()
        return False

    def _stop_ticks(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
        self._tick_id = 0

    # -- keeping in step with the popup -------------------------------------

    def _track(self) -> DeckTrack:
        assert self._parent is not None
        return deck_track(
            self._parent,
            self._monitor,
            inset=shell_config.NOTIFICATION_DECK_INSET,
            overlap=shell_config.NOTIFICATION_DECK_OVERLAP,
            direction=shell_config.NOTIFICATIONS_DECK_DIRECTION,
        )

    def _fit_resting_card(self) -> None:
        window = self._window
        machine = self.machine
        if (
            window is None
            or self._parent is None
            or machine.state is not DeckState.HIDDEN
            or machine.loading is not None
            or not window.get_mapped()
        ):
            return
        track = machine.track
        expected = self._track()
        if track is not None and (track.hidden_x, track.y, track.width, track.height) == (
            expected.hidden_x,
            expected.y,
            expected.width,
            expected.height,
        ):
            return
        window.hide()
        machine.unload()

    def _restack(self, _window: Gtk.Window, _event) -> bool:
        track = self.machine.track
        x = self.machine.x
        if track is not None and x is not None and not self.machine.animating:
            move_window_under(TITLE_NOTIFICATION_DECK, x, track.y, above_title=TITLE_NOTIFICATIONS)
        return False

    def _on_closed_outside(self, _window: Gtk.Window, _event) -> bool:
        """Closed by the compositor or a script: keep the window, start over."""
        self._stop_ticks()
        self._awaiting_paint = None
        self._awaiting_rows = None
        self.machine.reset()
        if self._window is not None:
            self._window.hide()
        return True

    # -- hover region ---------------------------------------------------------

    def _start_watch(self) -> None:
        if self._watch_id:
            return
        interval = max(10, int(shell_config.NOTIFICATIONS_HOVER_POLL_MS))
        self._watch_id = GLib.timeout_add(interval, self._sample_pointer)

    def _stop_watch(self) -> None:
        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = 0

    def _sample_pointer(self) -> bool:
        parent = self._parent
        position = self._pointer() if parent is not None else None
        if parent is None or position is None:
            return True
        card = self.machine.card_rect()
        self._pointer_on_card = card is not None and card.contains(*position)
        # A drag that started on the card (dismissing a row) may leave it.
        inside = (
            self._pointer_on_card
            or parent.contains(*position)
            or (card is not None and self._button_held_on_card())
        )
        settled = self.region.sample(inside, _now_ms())
        if settled is False:
            self._cancel_intent()
            self.machine.want(None, _now_ms())
        if settled is not None:
            self._report_hover(settled)
        return True

    def _button_held_on_card(self) -> bool:
        window = self._window
        surface = window.get_window() if window is not None else None
        seat = surface.get_display().get_default_seat() if surface is not None else None
        device = seat.get_pointer() if seat is not None else None
        if device is None:
            return False
        _window, _x, _y, mask = surface.get_device_position(device)
        return bool(
            mask
            & (
                Gdk.ModifierType.BUTTON1_MASK
                | Gdk.ModifierType.BUTTON2_MASK
                | Gdk.ModifierType.BUTTON3_MASK
            )
        )

    def _report_hover(self, inside: bool) -> None:
        if self._on_hover_changed is not None:
            self._on_hover_changed(inside)


def deck_motion() -> DeckMotion:
    theme = active_theme()
    reduced = bool(shell_config.NOTIFICATIONS_DECK_REDUCED_MOTION) or (
        theme is not None and not theme.animation.enabled
    )
    return DeckMotion(
        duration_ms=int(shell_config.NOTIFICATIONS_DECK_DURATION_MS),
        reduced=reduced,
    )


def _group_ids(group: list[NotificationSnapshot]) -> tuple[int, ...]:
    return tuple(snapshot.id for snapshot in group)


def _now_ms() -> float:
    return GLib.get_monotonic_time() / 1000.0
