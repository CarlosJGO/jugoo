"""Jugoo application launcher overlay."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ...config import LAUNCHER_MAX_HEIGHT, LAUNCHER_ROW_ICON_SIZE
from ...identity import TITLE_APP_LAUNCHER
from ...models import ApplicationsSnapshot, DesktopApplication, filter_applications
from ..pickers.overlay import PickerOverlay
from ..pickers.session import PickerSession
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


class AppLauncherWindow(PickerOverlay):
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
        super().__init__(
            shell_window,
            window_name="shell-app-launcher",
            title=TITLE_APP_LAUNCHER,
            namespace="shell-app-launcher",
            placeholder="Buscar aplicaciones...",
            empty_text="Sin resultados",
            session=PickerSession(columns=1),
        )
        self._on_launch = on_launch
        self._on_new_instance = on_new_instance
        self._on_pin_toggle = on_pin_toggle
        self._on_favorite_toggle = on_favorite_toggle
        self._on_refresh = on_refresh
        self._snapshot = ApplicationsSnapshot()
        self._rows: tuple[LauncherAppRow, ...] = ()

        self._list = Gtk.ListBox()
        self._list.get_style_context().add_class("launcher-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-activated", self._on_row_activated)
        self._list.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._list.connect("button-press-event", self._on_list_button_press)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(LAUNCHER_MAX_HEIGHT)
        scrolled.get_style_context().add_class("launcher-scroll")
        self._scrolled = scrolled
        scrolled.add(self._list)
        scrolled.connect("button-press-event", self._on_list_button_press)

        self._list_overlay = Gtk.Overlay()
        self._list_overlay.add(scrolled)
        self.content_box.pack_start(self._list_overlay, True, True, 0)

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

        self._search.connect("focus-in-event", self._on_search_focus)

    def open_launcher(self) -> None:
        self.open_picker()

    def close_launcher(self) -> None:
        self._dismiss_action_menu()
        self.close_picker()

    def toggle_launcher(self) -> None:
        self.toggle_picker()

    def set_snapshot(self, snapshot: ApplicationsSnapshot) -> None:
        self._snapshot = snapshot
        if self.get_visible():
            self._rebuild_rows(keep_selection=True)

    def on_prepare_open(self) -> None:
        self._snapshot = self._on_refresh()

    def on_query_changed(self, query: str) -> None:
        self._rebuild_rows()

    def on_activate(self) -> None:
        self._launch_selected()

    def on_selection_moved(self) -> None:
        index = self.session.selected_index
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            self._list.select_row(row)
            GLib.idle_add(self._ensure_row_visible, row)

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
        self.session.set_items(len(rows), reset_selection=not keep_selection)

        if rows:
            self.set_empty_visible(False)
            self._list.show()
            chosen = next((row for row in rows if row.application.id == selected_id), rows[0])
            if keep_selection:
                self.session.select_index(rows.index(chosen))
            else:
                chosen = rows[self.session.selected_index]
            self._list.select_row(chosen)
        else:
            self._list.hide()
            self.set_empty_visible(True)

    def _selected_application(self) -> DesktopApplication | None:
        row = self._list.get_selected_row()
        if isinstance(row, LauncherAppRow):
            return row.application
        return None

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

    def _on_key_press(self, widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key = event.keyval
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        super_space = (
            key in (Gdk.KEY_space, Gdk.KEY_KP_Space)
            and state & Gdk.ModifierType.SUPER_MASK
        )
        if key == Gdk.KEY_Escape and self._action_menu.get_visible():
            self._dismiss_action_menu()
            return True
        if super_space:
            self.close_launcher()
            return True
        return super()._on_key_press(widget, event)
