"""Full-height games panel that slides in from the right screen edge."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell, Pango

from ... import config as shell_config
from ...models import SteamCatalogSnapshot, SteamGame
from ...servicios.steam.images import placeholder_initials
from ...ui.edge_slide import EdgeSlide
from ...ui.starfield import install_starfield, resolve_event_bus
from ...window_identity import configure_interactive_popup, configure_toplevel, register_shell_popup
from ..pickers.emoji import origin_in
from ..pickers.session import scroll_to_reveal
from .presentation import (
    cover_size,
    filter_games,
    grid_layout,
    grid_move,
    image_size,
    status_message,
    summary_text,
)

PANEL_NAMESPACE = "shell-steam-games"
PANEL_TITLE = "Jugoo Steam"
GRID_COLUMN_SPACING = 12
GRID_ROW_SPACING = 14
# Margins, not CSS padding: GTK3 FlowBox counts its padding twice in
# width-for-height, so the viewport overflowed. The wider end inset keeps
# the overlay scrollbar off the last column.
GRID_MARGIN_START = 2
GRID_MARGIN_END = 12
GRID_MARGIN_TOP = 4
GRID_MARGIN_BOTTOM = 6

_ARROWS = {
    Gdk.KEY_Up: (0, -1),
    Gdk.KEY_KP_Up: (0, -1),
    Gdk.KEY_Down: (0, 1),
    Gdk.KEY_KP_Down: (0, 1),
    Gdk.KEY_Left: (-1, 0),
    Gdk.KEY_KP_Left: (-1, 0),
    Gdk.KEY_Right: (1, 0),
    Gdk.KEY_KP_Right: (1, 0),
}


class CoverLoader:
    """Decodes covers on worker threads; results come back on the GTK main loop."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="steam-covers")
        self._cache: dict[tuple[str, int, int], GdkPixbuf.Pixbuf] = {}
        self._lock = threading.Lock()
        self._closed = False

    def load(self, path: str, size: tuple[int, int], on_ready: Callable[[str, GdkPixbuf.Pixbuf | None], None]) -> None:
        key = (path, size[0], size[1])
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            on_ready(path, cached)
            return

        def work() -> None:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size[0], size[1], False)
            except GLib.Error as error:
                print(f"shell: steam: could not load cover {path}: {error.message}")
                pixbuf = None
            with self._lock:
                if self._closed:
                    return
                if pixbuf is not None:
                    self._cache[key] = pixbuf
            GLib.idle_add(lambda: (on_ready(path, pixbuf), False)[1])

        self._executor.submit(work)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cache.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)


class SteamGameCard(Gtk.FlowBoxChild):
    """Cover (or initials placeholder) with a spinner while a better image downloads."""

    def __init__(
        self,
        game: SteamGame,
        loader: CoverLoader,
        card_width: int,
        on_hover: Callable[["SteamGameCard"], None],
    ) -> None:
        super().__init__()
        self._loader = loader
        self._card_width = int(card_width)
        self._image_path = ""
        self._image_size = (0, 0)
        self.game = game
        self.set_can_focus(False)
        self.get_style_context().add_class("steam-grid-child")

        hover = Gtk.EventBox()
        hover.get_style_context().add_class("steam-card")
        hover.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        # Motion, not enter: keyboard scrolling under a still pointer must not steal the selection.
        hover.connect("motion-notify-event", lambda *_args: on_hover(self) or False)
        self.add(hover)

        # GtkEventBox ignores CSS padding (it only draws the border), so the
        # inset around the cover lives on this box.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("steam-card-body")
        hover.add(box)

        self._art = Gtk.Overlay()
        self._art.get_style_context().add_class("steam-card-art")
        self._art.set_halign(Gtk.Align.CENTER)
        box.pack_start(self._art, False, False, 0)

        placeholder = Gtk.Box()
        placeholder.get_style_context().add_class("steam-card-placeholder")
        self._initials = Gtk.Label()
        self._initials.get_style_context().add_class("steam-card-initials")
        self._initials.set_halign(Gtk.Align.CENTER)
        self._initials.set_valign(Gtk.Align.CENTER)
        placeholder.set_center_widget(self._initials)
        self._art.add(placeholder)

        self._image = Gtk.Image()
        self._image.set_halign(Gtk.Align.CENTER)
        self._image.set_valign(Gtk.Align.CENTER)
        self._image.set_no_show_all(True)
        self._art.add_overlay(self._image)

        self._spinner = Gtk.Spinner()
        self._spinner.get_style_context().add_class("steam-card-spinner")
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_size_request(24, 24)
        self._spinner.set_no_show_all(True)
        self._art.add_overlay(self._spinner)

        self._name = Gtk.Label()
        self._name.get_style_context().add_class("steam-card-name")
        self._name.set_line_wrap(True)
        self._name.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._name.set_lines(2)
        self._name.set_ellipsize(Pango.EllipsizeMode.END)
        self._name.set_justify(Gtk.Justification.CENTER)
        self._name.set_max_width_chars(1)
        box.pack_start(self._name, False, False, 0)

        self._apply_size()
        self.update(game)

    @property
    def card_width(self) -> int:
        return self._card_width

    def set_card_width(self, width: int) -> None:
        width = int(width)
        if width == self._card_width:
            return
        self._card_width = width
        self._apply_size()
        self._load_image()

    def update(self, game: SteamGame) -> None:
        self.game = game
        self._name.set_text(game.name)
        self._initials.set_text(placeholder_initials(game.name))
        self.set_tooltip_text(game.name)
        if game.image_pending:
            self._spinner.show()
            self._spinner.start()
        else:
            self._spinner.stop()
            self._spinner.hide()
        if game.image_path != self._image_path:
            self._image_path = game.image_path
            self._load_image()

    def _apply_size(self) -> None:
        width, height = cover_size(self._card_width)
        self._art.set_size_request(width, height)
        self._name.set_size_request(width, -1)

    def _load_image(self) -> None:
        if not self._image_path:
            self._image.clear()
            self._image.hide()
            return
        self._image_size = image_size(self.game.image_kind, self._card_width)
        self._loader.load(self._image_path, self._image_size, self._on_cover_ready)

    def _on_cover_ready(self, path: str, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        if path != self._image_path:
            return
        if pixbuf is None:
            self._image.hide()
            return
        if (pixbuf.get_width(), pixbuf.get_height()) != self._image_size:
            return
        self._image.set_from_pixbuf(pixbuf)
        self._image.show()


class SteamGamesPanel(Gtk.Window):
    """Layer over the whole output: dimmed backdrop plus the panel on the right edge."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_activate: Callable[[SteamGame], None] | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._on_activate = on_activate
        self._closing = False
        self._contents_shown = False
        self._loader = CoverLoader()
        self._snapshot = SteamCatalogSnapshot()
        self._cards: dict[str, SteamGameCard] = {}
        self._order: tuple[str, ...] = ()
        self._visible: tuple[SteamGameCard, ...] = ()
        self._rank: dict[str, int] = {}
        self._selected = -1
        self._columns = 1
        self._card_width = int(shell_config.STEAM_CARD_WIDTH)
        self._layout_width = -1
        self._layout_source_id = 0

        self.set_name("shell-steam-games")
        self.get_style_context().add_class("shell-steam-games")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=PANEL_TITLE)
        configure_interactive_popup(self)
        # A non-resizable window without a default size stays at its natural
        # size (200x1) and never takes the fullscreen configure from the layer.
        self.set_resizable(True)
        self._configure_layer_shell()

        root = Gtk.Overlay()
        self.add(root)

        self._backdrop = Gtk.EventBox()
        self._backdrop.get_style_context().add_class("steam-backdrop")
        self._backdrop.connect("button-press-event", self._on_backdrop_press)
        root.add(self._backdrop)

        self._slide = EdgeSlide()
        self._slide.set_halign(Gtk.Align.END)
        self._slide.set_valign(Gtk.Align.FILL)
        self._slide.set_frame_callback(self._backdrop.set_opacity)
        root.add_overlay(self._slide)

        host = Gtk.EventBox()
        host.get_style_context().add_class("steam-panel-host")
        host.connect("button-press-event", lambda _w, event: event.button == 1)
        self._slide.add(host)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        panel.get_style_context().add_class("launcher-card")
        panel.get_style_context().add_class("steam-panel")
        self._panel = panel
        install_starfield(host, panel, resolve_event_bus(shell_window))

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.get_style_context().add_class("steam-panel-header")
        title = Gtk.Label(label="Steam")
        title.get_style_context().add_class("steam-panel-title")
        title.set_xalign(0.0)
        header.pack_start(title, False, False, 0)
        self._summary = Gtk.Label()
        self._summary.get_style_context().add_class("steam-panel-summary")
        self._summary.set_xalign(0.0)
        header.pack_start(self._summary, False, False, 0)
        panel.pack_start(header, False, False, 0)

        self._search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._search_row.get_style_context().add_class("launcher-search-row")
        search_icon = Gtk.Image.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.MENU)
        search_icon.set_pixel_size(16)
        self._search_row.pack_start(search_icon, False, False, 0)
        self._search = Gtk.SearchEntry()
        self._search.get_style_context().add_class("launcher-search")
        self._search.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, None)
        self._search.set_placeholder_text("Buscar juegos…")
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)
        self._search_row.pack_start(self._search, True, True, 0)
        self._search_row.set_no_show_all(True)
        panel.pack_start(self._search_row, False, False, 0)

        self._status = Gtk.Label()
        self._status.get_style_context().add_class("launcher-empty")
        self._status.set_line_wrap(True)
        self._status.set_no_show_all(True)
        panel.pack_start(self._status, False, False, 0)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.get_style_context().add_class("steam-panel-scroll")
        panel.pack_start(self._scrolled, True, True, 0)

        self._grid = Gtk.FlowBox()
        self._grid.get_style_context().add_class("steam-grid")
        self._grid.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._grid.set_activate_on_single_click(True)
        self._grid.set_can_focus(False)
        self._grid.set_homogeneous(True)
        self._grid.set_valign(Gtk.Align.START)
        self._grid.set_min_children_per_line(1)
        self._grid.set_max_children_per_line(1)
        self._grid.set_column_spacing(GRID_COLUMN_SPACING)
        self._grid.set_row_spacing(GRID_ROW_SPACING)
        self._grid.set_margin_start(GRID_MARGIN_START)
        self._grid.set_margin_end(GRID_MARGIN_END)
        self._grid.set_margin_top(GRID_MARGIN_TOP)
        self._grid.set_margin_bottom(GRID_MARGIN_BOTTOM)
        self._grid.set_sort_func(self._sort_cards)
        self._grid.connect("child-activated", self._on_child_activated)
        self._scrolled.add(self._grid)
        # Measure the viewport, not the scrolled window: a classic scrollbar takes width.
        self._scrolled.get_child().connect("size-allocate", self._on_viewport_allocate)

        self._apply_width()
        self._slide.apply(0.0)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("destroy", self._on_destroy)

    @property
    def visible_games(self) -> tuple[SteamGame, ...]:
        return tuple(card.game for card in self._visible)

    @property
    def selected_game(self) -> SteamGame | None:
        if 0 <= self._selected < len(self._visible):
            return self._visible[self._selected].game
        return None

    @property
    def columns(self) -> int:
        return self._columns

    def is_effectively_open(self) -> bool:
        return self.get_visible() and not self._closing

    def open_panel(self) -> None:
        self._closing = False
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        if not self.get_visible():
            self._apply_width()
            self._slide.cancel()
            self._slide.apply(0.0)
            self._reset_search()
            if self._contents_shown:
                self.show()
            else:
                self.show_all()
                self._contents_shown = True
            self.present()
            self._search.grab_focus()
        self._slide.slide_in()

    def close_panel(self) -> None:
        if not self.get_visible():
            return
        self._closing = True
        self._slide.slide_out(on_complete=self._on_slide_done)

    def dismiss_immediately(self) -> None:
        self._slide.cancel()
        self._slide.apply(0.0)
        self._hide_now()

    def set_snapshot(self, snapshot: SteamCatalogSnapshot) -> None:
        self._snapshot = snapshot
        self._summary.set_text(summary_text(snapshot))
        self._set_search_visible(bool(snapshot.games))

        order = tuple(game.appid for game in snapshot.games)
        if order == self._order:
            for game in snapshot.games:
                self._cards[game.appid].update(game)
            self._apply_filter(keep_selection=True)
            return
        for child in self._grid.get_children():
            self._grid.remove(child)
            child.destroy()
        self._cards = {}
        for game in snapshot.games:
            card = SteamGameCard(game, self._loader, self._card_width, self._on_card_hover)
            self._cards[game.appid] = card
            self._grid.add(card)
        self._order = order
        if self._contents_shown:
            self._grid.show_all()
        self._apply_filter(keep_selection=True)

    def update_game(self, game: SteamGame) -> None:
        card = self._cards.get(game.appid)
        if card is not None:
            card.update(game)

    def select_index(self, index: int, *, reveal: bool = True) -> None:
        if not self._visible:
            self._selected = -1
            self._grid.unselect_all()
            return
        self._selected = max(0, min(len(self._visible) - 1, int(index)))
        card = self._visible[self._selected]
        self._grid.select_child(card)
        if reveal:
            GLib.idle_add(self._ensure_card_visible, card)

    def move_selection(self, dx: int, dy: int) -> None:
        target = grid_move(self._selected, len(self._visible), self._columns, dx, dy)
        if target >= 0 and target != self._selected:
            self.select_index(target)

    def activate_selected(self) -> None:
        game = self.selected_game
        if game is not None and self._on_activate is not None:
            self._on_activate(game)

    def layout_for_width(self, width: int) -> None:
        """Pick columns and card width for ``width`` px of grid viewport."""
        available = int(width) - GRID_MARGIN_START - GRID_MARGIN_END
        columns, card_width = grid_layout(
            available,
            int(shell_config.STEAM_CARD_WIDTH),
            GRID_COLUMN_SPACING,
            self._card_chrome(),
        )
        self._layout_width = int(width)
        if columns != self._columns:
            self._columns = columns
            self._grid.set_min_children_per_line(columns)
            self._grid.set_max_children_per_line(columns)
        if card_width != self._card_width:
            self._card_width = card_width
            for card in self._cards.values():
                card.set_card_width(card_width)
    def _card_chrome(self) -> int:
        card = next(iter(self._cards.values()), None)
        if card is None:
            return 0
        # The measured request catches extras the CSS boxes don't report.
        measured = card.get_preferred_width()[0] - card.card_width
        if measured > 0:
            return measured
        hover = card.get_child()
        total = 0
        for widget in (card, hover, hover.get_child()):
            context = widget.get_style_context()
            padding = context.get_padding(Gtk.StateFlags.NORMAL)
            border = context.get_border(Gtk.StateFlags.NORMAL)
            total += padding.left + padding.right + border.left + border.right
        return total

    def _on_viewport_allocate(self, _widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        if allocation.width <= 1 or allocation.width == self._layout_width:
            return
        if self._layout_source_id:
            GLib.source_remove(self._layout_source_id)
        width = allocation.width
        # Resizing children inside size-allocate would re-enter layout; defer one turn.
        self._layout_source_id = GLib.idle_add(self._run_layout, width)

    def _run_layout(self, width: int) -> bool:
        self._layout_source_id = 0
        self.layout_for_width(width)
        return False

    def _apply_filter(self, *, keep_selection: bool) -> None:
        query = self._search.get_text()
        previous = self.selected_game.appid if keep_selection and self.selected_game else None
        matches = filter_games(self._snapshot.games, query)
        self._rank = {game.appid: index for index, game in enumerate(matches)}
        for appid, card in self._cards.items():
            card.set_visible(appid in self._rank)
        self._grid.invalidate_sort()
        self._visible = tuple(self._cards[game.appid] for game in matches if game.appid in self._cards)

        message = status_message(self._snapshot, query, len(matches))
        self._status.set_text(message)
        self._status.set_visible(bool(message))

        index = 0
        if previous is not None and previous in self._rank:
            index = self._rank[previous]
        self.select_index(index, reveal=previous is not None)
        if previous is None:
            self._scrolled.get_vadjustment().set_value(0.0)

    def _set_search_visible(self, visible: bool) -> None:
        # show_all() is a no-op on a no_show_all widget, and show() alone
        # leaves the entry unrealized, so toggle the flag with the row.
        self._search_row.set_no_show_all(not visible)
        if visible:
            self._search_row.show_all()
        else:
            self._search_row.hide()

    def _reset_search(self) -> None:
        if self._search.get_text():
            self._search.set_text("")
        self._apply_filter(keep_selection=False)

    def _sort_cards(self, first: Gtk.FlowBoxChild, second: Gtk.FlowBoxChild) -> int:
        left = self._card_rank(first)
        right = self._card_rank(second)
        return (left > right) - (left < right)

    def _card_rank(self, child: Gtk.FlowBoxChild) -> int:
        if isinstance(child, SteamGameCard):
            return self._rank.get(child.game.appid, len(self._rank))
        return len(self._rank)

    def _ensure_card_visible(self, card: SteamGameCard, attempt: int = 0) -> bool:
        origin = origin_in(card, self._grid)
        adjustment = self._scrolled.get_vadjustment()
        height = card.get_allocated_height()
        page = adjustment.get_page_size()
        if origin is None or height <= 1 or page <= 0:
            if attempt < 2:
                GLib.idle_add(self._ensure_card_visible, card, attempt + 1)
            return False
        target = scroll_to_reveal(adjustment.get_value(), page, origin[1], height)
        if abs(target - adjustment.get_value()) > 0.5:
            adjustment.set_value(target)
        return False

    def _on_card_hover(self, card: SteamGameCard) -> None:
        if card in self._visible and self._visible.index(card) != self._selected:
            self.select_index(self._visible.index(card), reveal=False)

    def _on_child_activated(self, _grid: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        if child in self._visible:
            self.select_index(self._visible.index(child), reveal=False)
            self.activate_selected()

    def _on_search_changed(self, *_args) -> None:
        self._apply_filter(keep_selection=False)

    def _apply_width(self) -> None:
        width = max(300, int(shell_config.STEAM_PANEL_WIDTH))
        self._slide.set_size_request(width, -1)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, PANEL_NAMESPACE)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)

    def _on_slide_done(self, opened: bool) -> None:
        if not opened and self._closing:
            self._hide_now()

    def _hide_now(self) -> None:
        self._closing = False
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.hide()

    def _on_backdrop_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.close_panel()
        return True

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.close_panel()
            return True
        if self._closing:
            return False
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            self.activate_selected()
            return True
        if key in _ARROWS:
            self.move_selection(*_ARROWS[key])
            return True
        return False

    def _on_destroy(self, *_args) -> None:
        self._slide.cancel()
        if self._layout_source_id:
            GLib.source_remove(self._layout_source_id)
            self._layout_source_id = 0
        self._loader.close()
