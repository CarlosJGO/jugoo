"""Jugoo application launcher overlay."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango

from ...config import (
    LAUNCHER_LIST_SPACING,
    LAUNCHER_MAX_HEIGHT,
    LAUNCHER_ROW_ICON_SIZE,
    LAUNCHER_WIDTH,
)
from ...models import ApplicationsSnapshot, DesktopApplication, filter_applications
from ...popup_handle import hide_popup, present_popup
from ...window_identity import TITLE_APP_LAUNCHER, configure_interactive_popup, configure_toplevel, register_shell_popup


class LauncherAppRow(Gtk.ListBoxRow):
    """One searchable application with an obvious pin control."""

    def __init__(
        self,
        application: DesktopApplication,
        *,
        pinned: bool,
        on_pin_toggle: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.application = application
        self.get_style_context().add_class("launcher-row")
        if pinned:
            self.get_style_context().add_class("launcher-row-pinned")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.get_style_context().add_class("launcher-row-content")
        self.add(content)

        icon = Gtk.Image.new_from_icon_name(application.icon, Gtk.IconSize.DIALOG)
        icon.set_pixel_size(LAUNCHER_ROW_ICON_SIZE)
        icon.get_style_context().add_class("launcher-row-icon")
        content.pack_start(icon, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(label=application.name, xalign=0)
        name.get_style_context().add_class("launcher-row-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        labels.pack_start(name, False, False, 0)
        if application.comment:
            comment = Gtk.Label(label=application.comment, xalign=0)
            comment.get_style_context().add_class("launcher-row-comment")
            comment.set_ellipsize(Pango.EllipsizeMode.END)
            labels.pack_start(comment, False, False, 0)
        content.pack_start(labels, True, True, 0)

        pin_button = Gtk.Button()
        pin_button.set_relief(Gtk.ReliefStyle.NONE)
        pin_button.get_style_context().add_class("launcher-pin-button")
        if pinned:
            pin_button.get_style_context().add_class("pinned")
        pin_button.set_tooltip_text("Desfijar" if pinned else "Fijar en la barra")
        pin_icon = Gtk.Image.new_from_icon_name(
            "starred-symbolic" if pinned else "non-starred-symbolic",
            Gtk.IconSize.MENU,
        )
        pin_icon.set_pixel_size(16)
        pin_button.add(pin_icon)
        pin_button.connect("clicked", lambda *_args: on_pin_toggle(application.id))
        content.pack_end(pin_button, False, False, 0)


class AppLauncherWindow(Gtk.Window):
    """Centered GtkLayerShell overlay with in-memory application search."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_launch: Callable[[str], None],
        on_pin_toggle: Callable[[str], None],
        on_refresh: Callable[[], ApplicationsSnapshot],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._on_launch = on_launch
        self._on_pin_toggle = on_pin_toggle
        self._on_refresh = on_refresh
        self._snapshot = ApplicationsSnapshot()
        self._rows: tuple[LauncherAppRow, ...] = ()
        self._closing = False

        self.set_name("shell-app-launcher")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_APP_LAUNCHER)
        configure_interactive_popup(self)
        self.set_default_size(LAUNCHER_WIDTH, -1)
        self._configure_layer_shell()

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
        card.connect("button-press-event", lambda *_args: True)
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
        self._search.set_placeholder_text("Buscar aplicaciones...")
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_query_changed)
        self._search.connect("activate", self._on_search_activate)
        search_row.pack_start(self._search, True, True, 0)
        outer.pack_start(search_row, False, False, 0)

        self._empty = Gtk.Label(label="Sin resultados")
        self._empty.get_style_context().add_class("launcher-empty")
        self._empty.set_no_show_all(True)
        outer.pack_start(self._empty, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(LAUNCHER_MAX_HEIGHT)
        scrolled.get_style_context().add_class("launcher-scroll")
        outer.pack_start(scrolled, True, True, 0)
        self._scrolled = scrolled

        self._list = Gtk.ListBox()
        self._list.get_style_context().add_class("launcher-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-activated", self._on_row_activated)
        scrolled.add(self._list)

        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("map", self._on_map)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-app-launcher")
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

    def set_snapshot(self, snapshot: ApplicationsSnapshot) -> None:
        self._snapshot = snapshot
        if self.get_visible():
            self._rebuild_rows(keep_selection=True)

    def open_launcher(self) -> None:
        self._closing = False
        self._snapshot = self._on_refresh()
        self._search.set_text("")
        self._rebuild_rows()
        present_popup(self)
        GLib.idle_add(self._focus_search)

    def close_launcher(self) -> None:
        if self._closing or not self.get_visible():
            hide_popup(self)
            return
        self._closing = True
        hide_popup(self)

    def toggle_launcher(self) -> None:
        if self.get_visible():
            self.close_launcher()
        else:
            self.open_launcher()

    def _focus_search(self) -> bool:
        self._search.grab_focus()
        return False

    def _on_map(self, *_args) -> None:
        self._focus_search()

    def _on_query_changed(self, *_args) -> None:
        self._rebuild_rows()

    def _rebuild_rows(self, *, keep_selection: bool = False) -> None:
        selected_id = None
        if keep_selection:
            selected = self._selected_application()
            if selected is not None:
                selected_id = selected.id
        for child in list(self._list.get_children()):
            self._list.remove(child)

        pinned = set(self._snapshot.pinned_ids)
        matches = filter_applications(
            self._snapshot.applications,
            self._search.get_text(),
            self._snapshot.pinned_ids,
        )
        rows: list[LauncherAppRow] = []
        for application in matches:
            row = LauncherAppRow(
                application,
                pinned=application.id in pinned,
                on_pin_toggle=self._on_pin_toggle,
            )
            self._list.add(row)
            rows.append(row)
        self._rows = tuple(rows)
        self._list.show_all()

        if rows:
            self._empty.hide()
            self._list.show()
            chosen = next((row for row in rows if row.application.id == selected_id), rows[0])
            self._list.select_row(chosen)
        else:
            self._list.hide()
            self._empty.show()

    def _selected_application(self) -> DesktopApplication | None:
        row = self._list.get_selected_row()
        if isinstance(row, LauncherAppRow):
            return row.application
        return None

    def _move_selection(self, delta: int) -> None:
        if not self._rows:
            return
        current = self._list.get_selected_row()
        try:
            index = self._rows.index(current) if current in self._rows else 0
        except ValueError:
            index = 0
        index = max(0, min(len(self._rows) - 1, index + delta))
        row = self._rows[index]
        self._list.select_row(row)
        GLib.idle_add(self._ensure_row_visible, row)

    def _ensure_row_visible(self, row: Gtk.ListBoxRow) -> bool:
        alloc = row.get_allocation()
        adj = self._scrolled.get_vadjustment()
        if adj is None:
            return False
        value = adj.get_value()
        page = adj.get_page_size()
        if alloc.y < value:
            adj.set_value(alloc.y)
        elif alloc.y + alloc.height > value + page:
            adj.set_value(alloc.y + alloc.height - page)
        return False

    def _launch_selected(self) -> None:
        application = self._selected_application()
        if application is None:
            return
        self.close_launcher()
        self._on_launch(application.id)

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, LauncherAppRow):
            self.close_launcher()
            self._on_launch(row.application.id)

    def _on_search_activate(self, *_args) -> None:
        self._launch_selected()

    def _on_backdrop_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.close_launcher()
        return True

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key = event.keyval
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        super_space = (
            key in (Gdk.KEY_space, Gdk.KEY_KP_Space)
            and state & Gdk.ModifierType.SUPER_MASK
        )
        if key == Gdk.KEY_Escape or super_space:
            self.close_launcher()
            return True
        if key in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._move_selection(1)
            return True
        if key in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._move_selection(-1)
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._launch_selected()
            return True
        return False
