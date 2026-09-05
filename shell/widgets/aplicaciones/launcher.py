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
    LAUNCHER_MAX_HEIGHT,
    LAUNCHER_ROW_ICON_SIZE,
    LAUNCHER_WIDTH,
)
from ...models import ApplicationsSnapshot, DesktopApplication, filter_applications
from ...popup_handle import hide_popup, present_popup
from ...window_identity import TITLE_APP_LAUNCHER, configure_interactive_popup, configure_toplevel, register_shell_popup
from .context_menu import fill_application_menu


class LauncherAppRow(Gtk.ListBoxRow):
    """One searchable application with independent favorite and dock-pin state."""

    def __init__(
        self,
        application: DesktopApplication,
        *,
        favorite: bool,
        pinned: bool,
        on_open: Callable[[str], None],
        on_new_instance: Callable[[str], None],
        on_favorite_toggle: Callable[[str], None],
        on_pin_toggle: Callable[[str], None],
        on_context_menu: Callable[["LauncherAppRow"], None],
    ) -> None:
        super().__init__()
        self.application = application
        self._favorite = favorite
        self._pinned = pinned
        self._on_open = on_open
        self._on_new_instance = on_new_instance
        self._on_favorite_toggle = on_favorite_toggle
        self._on_pin_toggle = on_pin_toggle
        self._on_context_menu = on_context_menu
        self.get_style_context().add_class("launcher-row")
        if favorite:
            self.get_style_context().add_class("launcher-row-favorite")
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

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        status.get_style_context().add_class("launcher-row-status")
        content.pack_end(status, False, False, 0)

        pin_icon = Gtk.Image.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU)
        pin_icon.set_pixel_size(16)
        pin_icon.get_style_context().add_class("launcher-pin-indicator")
        pin_icon.set_tooltip_text("Fijada en el dock")
        pin_icon.set_no_show_all(True)
        if pinned:
            pin_icon.show()
        else:
            pin_icon.hide()
        status.pack_start(pin_icon, False, False, 0)

        favorite_button = Gtk.Button()
        favorite_button.set_relief(Gtk.ReliefStyle.NONE)
        favorite_button.get_style_context().add_class("launcher-favorite-button")
        if favorite:
            favorite_button.get_style_context().add_class("favorited")
        favorite_button.set_tooltip_text(
            "Quitar de favoritos" if favorite else "Marcar como favorito"
        )
        favorite_icon = Gtk.Image.new_from_icon_name(
            "starred-symbolic" if favorite else "non-starred-symbolic",
            Gtk.IconSize.MENU,
        )
        favorite_icon.set_pixel_size(16)
        favorite_button.add(favorite_icon)
        favorite_button.connect("clicked", lambda *_args: on_favorite_toggle(application.id))
        favorite_button.connect("button-press-event", self._on_button_press)
        status.pack_start(favorite_button, False, False, 0)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_button_press)

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            return False
        self._on_context_menu(self)
        return True

    def menu_entries(self):
        favorite_label = "Quitar de favoritos" if self._favorite else "Marcar como favorito"
        pin_label = "Desfijar del dock" if self._pinned else "Fijar en dock"
        return (
            ("Abrir", lambda: self._on_open(self.application.id)),
            ("Nueva instancia", lambda: self._on_new_instance(self.application.id)),
            None,
            (favorite_label, lambda: self._on_favorite_toggle(self.application.id)),
            (pin_label, lambda: self._on_pin_toggle(self.application.id)),
        )


class AppLauncherWindow(Gtk.Window):
    """Centered GtkLayerShell overlay with in-memory application search."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_launch: Callable[[str], None],
        on_new_instance: Callable[[str], None],
        on_pin_toggle: Callable[[str], None],
        on_favorite_toggle: Callable[[str], None],
        on_refresh: Callable[[], ApplicationsSnapshot],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._on_launch = on_launch
        self._on_new_instance = on_new_instance
        self._on_pin_toggle = on_pin_toggle
        self._on_favorite_toggle = on_favorite_toggle
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
        self._search.set_placeholder_text("Buscar aplicaciones...")
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_query_changed)
        self._search.connect("activate", self._on_search_activate)
        self._search.connect("focus-in-event", self._on_search_focus)
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
        self._scrolled = scrolled

        self._list = Gtk.ListBox()
        self._list.get_style_context().add_class("launcher-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-activated", self._on_row_activated)
        self._list.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._list.connect("button-press-event", self._on_list_button_press)
        scrolled.add(self._list)
        scrolled.connect("button-press-event", self._on_list_button_press)

        self._list_overlay = Gtk.Overlay()
        self._list_overlay.add(scrolled)
        outer.pack_start(self._list_overlay, True, True, 0)

        self._menu_catcher = Gtk.EventBox()
        self._menu_catcher.get_style_context().add_class("launcher-menu-catcher")
        self._menu_catcher.set_halign(Gtk.Align.FILL)
        self._menu_catcher.set_valign(Gtk.Align.FILL)
        self._menu_catcher.set_hexpand(True)
        self._menu_catcher.set_vexpand(True)
        self._menu_catcher.set_no_show_all(True)
        self._menu_catcher.connect("button-press-event", self._on_menu_catcher_press)
        self._list_overlay.add_overlay(self._menu_catcher)
        self._menu_catcher.hide()

        self._action_menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._action_menu.get_style_context().add_class("launcher-action-menu")
        self._action_menu.set_halign(Gtk.Align.END)
        self._action_menu.set_valign(Gtk.Align.START)
        self._action_menu.set_no_show_all(True)
        self._list_overlay.add_overlay(self._action_menu)
        self._action_menu.hide()

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
        self._dismiss_action_menu()
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
        self._dismiss_action_menu()
        selected_id = None
        if keep_selection:
            selected = self._selected_application()
            if selected is not None:
                selected_id = selected.id
        for child in list(self._list.get_children()):
            self._list.remove(child)

        matches = filter_applications(
            self._snapshot.applications,
            self._search.get_text(),
            self._snapshot.favorite_ids,
        )
        rows: list[LauncherAppRow] = []
        for application in matches:
            row = LauncherAppRow(
                application,
                favorite=self._snapshot.is_favorite(application.id),
                pinned=self._snapshot.is_pinned(application.id),
                on_open=self._open_application,
                on_new_instance=self._new_instance_application,
                on_favorite_toggle=self._on_favorite_toggle,
                on_pin_toggle=self._on_pin_toggle,
                on_context_menu=self._show_row_menu,
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
        self._dismiss_action_menu()
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

    def _dismiss_action_menu(self) -> None:
        self._menu_catcher.hide()
        self._menu_catcher.set_no_show_all(True)
        self._action_menu.hide()
        self._action_menu.set_no_show_all(True)
        for child in list(self._action_menu.get_children()):
            self._action_menu.remove(child)

    def _show_row_menu(self, row: LauncherAppRow) -> None:
        self._list.select_row(row)
        self._menu_catcher.set_no_show_all(False)
        self._menu_catcher.show()
        self._action_menu.set_no_show_all(False)
        fill_application_menu(self._action_menu, row.menu_entries(), self._on_action_picked)
        translated = row.translate_coordinates(self._list_overlay, 0, 0)
        y = 0
        if translated:
            if len(translated) == 3:
                _ok, _x, y = translated
            else:
                _x, y = translated
        _min_h, menu_h = self._action_menu.get_preferred_height()
        overlay_h = self._list_overlay.get_allocated_height()
        if overlay_h > 0 and menu_h > 0:
            y = max(0, min(int(y), overlay_h - menu_h))
        else:
            y = max(0, int(y))
        self._action_menu.set_margin_top(y)
        self._action_menu.set_margin_end(8)
        self._action_menu.show_all()

    def _on_action_picked(self, callback: Callable[[], None]) -> None:
        self._dismiss_action_menu()
        callback()

    def _on_menu_catcher_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            y = int(event.y)
            translated = widget.translate_coordinates(self._list, int(event.x), int(event.y))
            if translated:
                if len(translated) == 3:
                    _ok, _x, y = translated
                else:
                    _x, y = translated
            row = self._list.get_row_at_y(int(y))
            if isinstance(row, LauncherAppRow):
                self._show_row_menu(row)
                return True
        self._dismiss_action_menu()
        return True

    def _on_search_focus(self, *_args) -> bool:
        self._dismiss_action_menu()
        return False

    def _on_list_button_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            if self._action_menu.get_visible():
                self._dismiss_action_menu()
            return False
        y = int(event.y)
        if widget is not self._list:
            translated = widget.translate_coordinates(self._list, int(event.x), int(event.y))
            if not translated:
                return False
            if len(translated) == 3:
                _ok, _x, y = translated
            else:
                _x, y = translated
        row = self._list.get_row_at_y(int(y))
        if isinstance(row, LauncherAppRow):
            self._show_row_menu(row)
            return True
        self._dismiss_action_menu()
        return True

    def _on_card_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1 and self._action_menu.get_visible():
            self._dismiss_action_menu()
            return True
        return event.button == 1

    def _open_application(self, app_id: str) -> None:
        self.close_launcher()
        self._on_launch(app_id)

    def _new_instance_application(self, app_id: str) -> None:
        self.close_launcher()
        self._on_new_instance(app_id)

    def _launch_selected(self) -> None:
        application = self._selected_application()
        if application is None:
            return
        self._open_application(application.id)

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if self._action_menu.get_visible():
            self._dismiss_action_menu()
            return
        if isinstance(row, LauncherAppRow):
            self._open_application(row.application.id)

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
            if self._action_menu.get_visible() and key == Gdk.KEY_Escape:
                self._dismiss_action_menu()
                return True
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
