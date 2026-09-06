"""Centered GtkLayerShell overlay shared by Search, Clipboard, and Emoji pickers."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ...config import LAUNCHER_MAX_HEIGHT, LAUNCHER_WIDTH
from ...popup_handle import hide_popup, present_popup
from ...window_identity import configure_interactive_popup, configure_toplevel, register_shell_popup
from .session import ACTION_CLOSE, ACTION_MOVED, ACTION_SELECT, PickerSession


class PickerOverlay(Gtk.Window):
    """Fullscreen exclusive overlay with the Search card chrome."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        window_name: str,
        title: str,
        namespace: str,
        placeholder: str,
        empty_text: str,
        session: PickerSession,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._session = session
        self._closing = False

        self.set_name(window_name)
        self.get_style_context().add_class("shell-picker")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=title)
        configure_interactive_popup(self)
        self.set_default_size(LAUNCHER_WIDTH, -1)
        self._configure_layer_shell(namespace)

        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("launcher-backdrop")
        backdrop.connect("button-press-event", self._on_backdrop_press)
        self.add(backdrop)

        aligner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        aligner.set_halign(Gtk.Align.CENTER)
        aligner.set_valign(Gtk.Align.CENTER)
        backdrop.add(aligner)

        card = Gtk.EventBox()
        card.get_style_context().add_class("launcher-card-host")
        card.connect("button-press-event", self._on_card_press)
        aligner.pack_start(card, False, False, 0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.get_style_context().add_class("launcher-card")
        outer.set_size_request(LAUNCHER_WIDTH, -1)
        card.add(outer)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_row.get_style_context().add_class("launcher-search-row")
        search_icon = Gtk.Image.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.MENU)
        search_icon.set_pixel_size(16)
        search_row.pack_start(search_icon, False, False, 0)

        self._search = Gtk.SearchEntry()
        self._search.get_style_context().add_class("launcher-search")
        self._search.set_placeholder_text(placeholder)
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)
        self._search.connect("activate", self._on_search_activate)
        search_row.pack_start(self._search, True, True, 0)
        outer.pack_start(search_row, False, False, 0)

        self._empty = Gtk.Label(label=empty_text)
        self._empty.get_style_context().add_class("launcher-empty")
        self._empty.set_no_show_all(True)
        outer.pack_start(self._empty, False, False, 0)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(self.content_box, True, True, 0)

        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("map", self._on_map)

    @property
    def session(self) -> PickerSession:
        return self._session

    def attach_scrolled(self, child: Gtk.Widget) -> Gtk.ScrolledWindow:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(LAUNCHER_MAX_HEIGHT)
        scrolled.get_style_context().add_class("launcher-scroll")
        scrolled.add(child)
        self.content_box.pack_start(scrolled, True, True, 0)
        return scrolled

    def open_picker(self) -> None:
        self._closing = False
        self._session.open_session(self._session.item_count)
        self.on_prepare_open()
        if self._search.get_text():
            self._search.set_text("")
        else:
            self.on_query_changed("")
        present_popup(self)
        GLib.idle_add(self._focus_search)

    def close_picker(self) -> None:
        self._session.close_session()
        if self._closing or not self.get_visible():
            hide_popup(self)
            return
        self._closing = True
        hide_popup(self)

    def toggle_picker(self) -> None:
        if self.get_visible():
            self.close_picker()
        else:
            self.open_picker()

    def set_empty_visible(self, visible: bool) -> None:
        if visible:
            self._empty.show()
        else:
            self._empty.hide()

    def on_prepare_open(self) -> None:
        """Subclasses refresh backing data before the overlay is shown."""

    def on_query_changed(self, query: str) -> None:
        """Subclasses rebuild visible results for ``query``."""

    def on_activate(self) -> None:
        """Subclasses apply the selected item."""

    def on_selection_moved(self) -> None:
        """Subclasses sync widgets to ``session.selected_index``."""

    def _configure_layer_shell(self, namespace: str) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, namespace)
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

    def _focus_search(self) -> bool:
        self._search.grab_focus()
        return False

    def _on_map(self, *_args) -> None:
        self._focus_search()

    def _on_search_changed(self, *_args) -> None:
        query = self._search.get_text()
        self._session.query = query
        self.on_query_changed(query)

    def _on_search_activate(self, *_args) -> None:
        self.on_activate()

    def _on_backdrop_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.close_picker()
        return True

    def _on_card_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        return event.button == 1

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key_name = Gdk.keyval_name(event.keyval) or ""
        result = self._session.handle_key(key_name)
        if result == ACTION_CLOSE:
            self.close_picker()
            return True
        if result == ACTION_SELECT:
            self.on_activate()
            return True
        if result == ACTION_MOVED:
            self.on_selection_moved()
            return True
        return False
