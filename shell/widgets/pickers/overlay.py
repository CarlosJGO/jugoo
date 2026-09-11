"""Puertas: centered GtkLayerShell overlays shared by Search, Clipboard, and Emoji.

Open/close with a door wipe from the card center (vertical by default,
horizontal when requested).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ...config import LAUNCHER_MAX_HEIGHT, LAUNCHER_WIDTH
from ...ui.door import DoorAxis, DoorClip
from ...ui.starfield import install_starfield, resolve_event_bus
from ...ui.theme import active_theme
from ...window_identity import configure_interactive_popup, configure_toplevel, register_shell_popup
from .session import ACTION_CLOSE, ACTION_MOVED, ACTION_SELECT, PickerSession


class PickerOverlay(Gtk.Window):
    """Fullscreen exclusive puerta with the Search card chrome."""

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
        layout: str = "simple",
        card_width: int | None = None,
        card_height: int = -1,
        door_axis: DoorAxis = "vertical",
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._session = session
        self._closing = False
        self._opening = False
        self._present_generation = 0
        self._layout = layout
        self._focus_search_on_open = True
        self._card_height_request = card_height
        self._resolved_card_width = card_width if card_width is not None else LAUNCHER_WIDTH
        self._seed_door_height = card_height if card_height > 0 else LAUNCHER_MAX_HEIGHT

        self.set_name(window_name)
        self.get_style_context().add_class("shell-picker")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=title)
        configure_interactive_popup(self)
        self.set_default_size(self._resolved_card_width, card_height)
        self._configure_layer_shell(namespace)

        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("launcher-backdrop")
        backdrop.connect("button-press-event", self._on_backdrop_press)
        self.add(backdrop)

        aligner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        aligner.set_halign(Gtk.Align.CENTER)
        aligner.set_valign(Gtk.Align.CENTER)
        backdrop.add(aligner)

        self._door = DoorClip(axis=door_axis)
        aligner.pack_start(self._door, False, False, 0)

        card = Gtk.EventBox()
        card.get_style_context().add_class("launcher-card-host")
        card.connect("button-press-event", self._on_card_press)
        self._card_host = card
        self._door.set_child(card)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.get_style_context().add_class("launcher-card")
        outer.set_size_request(self._resolved_card_width, card_height)
        install_starfield(
            card,
            outer,
            resolve_event_bus(shell_window),
            corner_radius=16.0,
        )

        self._search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._search_row.get_style_context().add_class("launcher-search-row")
        search_icon = Gtk.Image.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.MENU)
        search_icon.set_pixel_size(16)
        self._search_row.pack_start(search_icon, False, False, 0)

        self._search = Gtk.SearchEntry()
        self._search.get_style_context().add_class("launcher-search")
        self._search.set_placeholder_text(placeholder)
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)
        self._search.connect("activate", self._on_search_activate)
        self._search_row.pack_start(self._search, True, True, 0)

        self._empty = Gtk.Label(label=empty_text)
        self._empty.get_style_context().add_class("launcher-empty")
        self._empty.set_no_show_all(True)
        self.left_slot: Gtk.Box | None = None
        self.right_slot: Gtk.Box | None = None
        self.center_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.center_slot.pack_start(self._search_row, False, False, 0)
        self.center_slot.pack_start(self._empty, False, False, 0)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.center_slot.pack_start(self.content_box, True, True, 0)

        self._shell_outer = outer
        if self._layout == "control_center":
            # Three sibling columns in one shell. Shared height; only center width flexes.
            from ...config import (
                CONTROL_CENTER_CENTER_MIN_WIDTH,
                CONTROL_CENTER_HEIGHT,
                CONTROL_CENTER_LEFT_WIDTH,
                CONTROL_CENTER_RIGHT_WIDTH,
            )

            outer.get_style_context().add_class("control-center-shell")
            outer.set_size_request(self._resolved_card_width, CONTROL_CENTER_HEIGHT)

            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            body.get_style_context().add_class("control-center-body")
            body.set_hexpand(True)
            body.set_vexpand(True)
            outer.pack_start(body, True, True, 0)

            self.left_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.left_slot.get_style_context().add_class("control-center-left")
            self.left_slot.set_size_request(CONTROL_CENTER_LEFT_WIDTH, -1)
            self.left_slot.set_hexpand(False)
            self.left_slot.set_vexpand(True)
            body.pack_start(self.left_slot, False, True, 0)

            self.center_slot.get_style_context().add_class("control-center-center")
            self.center_slot.set_size_request(CONTROL_CENTER_CENTER_MIN_WIDTH, -1)
            self.center_slot.set_hexpand(True)
            self.center_slot.set_vexpand(True)
            body.pack_start(self.center_slot, True, True, 0)

            self.right_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.right_slot.get_style_context().add_class("control-center-right")
            self.right_slot.set_size_request(CONTROL_CENTER_RIGHT_WIDTH, -1)
            self.right_slot.set_hexpand(False)
            self.right_slot.set_vexpand(True)
            body.pack_start(self.right_slot, False, True, 0)
        else:
            outer.pack_start(self.center_slot, True, True, 0)

        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("map", self._on_map)
        self.connect("destroy", self._on_destroy)

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

    def open_picker(self, *, focus_search: bool = True, reset_query: bool = True) -> None:
        self._closing = False
        self._focus_search_on_open = focus_search
        self._session.open_session(self._session.item_count)
        self.on_prepare_open()
        if reset_query:
            if self._search.get_text():
                self._search.set_text("")
            else:
                self.on_query_changed("")
        self._present_door()
        if focus_search:
            GLib.idle_add(self._focus_search)

    def close_picker(self) -> None:
        self._session.close_session()
        self._present_generation += 1
        self._opening = False
        if self._closing or not self.get_visible():
            self._door.cancel()
            self.hide()
            return
        self._closing = True
        self._hide_door()

    def toggle_picker(self) -> None:
        if self.is_effectively_open():
            self.close_picker()
        else:
            self.open_picker()

    def is_effectively_open(self) -> bool:
        """True when the puerta is shown and not stuck on a blank first map."""
        if not self.get_visible():
            return False
        if self._closing:
            return True
        if self._opening or self._door.animating:
            return True
        return self._door.progress > 0.05

    def warm_up(self) -> None:
        """Build content and realize so the first bind does not race layout."""
        self.on_prepare_open()
        self.on_query_changed(self._search.get_text())
        if not self.get_realized():
            self.realize()
        self._card_host.set_size_request(self._resolved_card_width, self._card_height_request)
        self._door.seed_size(self._resolved_card_width, self._seed_door_height)
        self._door.capture_full_size()
        self._door.apply(1.0)

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
        if self._focus_search_on_open:
            self._focus_search()

    def _on_destroy(self, *_args) -> None:
        self._door.cancel()

    def _on_search_changed(self, *_args) -> None:
        query = self._search.get_text()
        self._session.query = query
        self.on_query_changed(query)
        if self.get_visible() and not self._door.animating and not self._closing:
            self._door.capture_full_size()
            self._door.apply(1.0)

    def set_search_row_visible(self, visible: bool) -> None:
        if visible:
            self._search_row.show()
        else:
            self._search_row.hide()

    def set_shell_size(self, width: int, height: int) -> None:
        """Resize the control-center shell (center column may grow with content)."""
        if hasattr(self, "_shell_outer") and self._shell_outer is not None:
            self._shell_outer.set_size_request(width, height)
            self.queue_resize()
            if self.get_visible() and not self._door.animating and not self._closing:
                self._door.capture_full_size()
                self._door.apply(1.0)

    def focus_search(self) -> None:
        self._focus_search()

    def _animations_enabled(self) -> bool:
        theme = active_theme()
        return theme is None or theme.animation.enabled

    def _present_door(self) -> None:
        # Already fully open: refresh size after content changes, do not replay the door.
        if (
            self.get_visible()
            and not self._closing
            and not self._opening
            and self._door.progress >= 1.0
            and not self._door.animating
        ):
            self._door.capture_full_size()
            self._door.apply(1.0)
            return

        reversing = (
            self.get_visible()
            and (self._door.animating or self._door.progress < 1.0)
            and not self._opening
        )
        start = self._door.progress if reversing else 0.0
        self._door.cancel()
        self._closing = False
        self._opening = True
        self._present_generation += 1
        generation = self._present_generation

        self._card_host.set_size_request(self._resolved_card_width, self._card_height_request)
        self._door.seed_size(self._resolved_card_width, self._seed_door_height)
        # Stay opaque: opacity 0→1 on a fullscreen layer was the black flash.
        self.set_opacity(1.0)
        self._door.apply(0.0)
        self.show_all()
        self.present()

        if reversing:
            self._door.capture_full_size()
            if not self._animations_enabled():
                self._door.apply(1.0)
                self._opening = False
                return
            self._door.open(from_progress=start, on_complete=self._on_door_open_complete)
            return

        # Wait for allocate/realize before measuring — first map used to get 1×1.
        GLib.idle_add(self._begin_door_open, generation)

    def _begin_door_open(self, generation: int) -> bool:
        if generation != self._present_generation or self._closing:
            return False
        self._door.capture_full_size()
        if not self._door.size_ready():
            # Content not laid out yet; try once more on the next idle.
            GLib.idle_add(self._begin_door_open_retry, generation)
            return False
        self._start_measured_open(generation)
        return False

    def _begin_door_open_retry(self, generation: int) -> bool:
        if generation != self._present_generation or self._closing:
            return False
        self._door.capture_full_size()
        self._start_measured_open(generation)
        return False

    def _start_measured_open(self, generation: int) -> None:
        if generation != self._present_generation or self._closing:
            return
        self._door.apply(0.0)
        if not self._animations_enabled():
            self._door.apply(1.0)
            self._opening = False
            return
        self._door.open(from_progress=0.0, on_complete=self._on_door_open_complete)

    def _on_door_open_complete(self, _opening: bool) -> None:
        self._opening = False

    def _hide_door(self) -> None:
        if not self.get_visible():
            self.hide()
            self._closing = False
            self._opening = False
            return

        if self._door.progress >= 1.0:
            self._door.capture_full_size()
            self._door.apply(1.0)

        if not self._animations_enabled():
            self._door.cancel()
            self.hide()
            self._door.apply(1.0)
            self._closing = False
            self._opening = False
            return

        self._door.close(on_complete=self._on_door_close_complete)

    def _on_door_close_complete(self, _opening: bool) -> None:
        self.hide()
        self._door.apply(1.0)
        self._card_host.set_size_request(self._resolved_card_width, self._card_height_request)
        self._closing = False
        self._opening = False

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
