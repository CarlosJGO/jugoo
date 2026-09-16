"""Jugoo application launcher puerta (Search / control center)."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ...config import (
    CONTROL_CENTER_CENTER_MIN_WIDTH,
    CONTROL_CENTER_CENTER_SETTINGS_WIDTH,
    CONTROL_CENTER_HEIGHT,
    CONTROL_CENTER_SETTINGS_WIDTH,
    CONTROL_CENTER_WIDTH,
    LAUNCHER_MAX_HEIGHT,
    LAUNCHER_ROW_ICON_SIZE,
)
from ...eventbus import EventBus
from ...identity import TITLE_APP_LAUNCHER
from ...models import ApplicationsSnapshot, DesktopApplication, filter_applications
from ...servicios.sistema.system import SystemStatsService
from ...settings.manager import SettingsManager
from ...settings.schema import CategoryId
from ..configuraciones.pane import SEARCH_DESTINATION, Destination, build_settings_nav, show_settings_category
from ..pickers.overlay import PickerOverlay
from ..pickers.session import PickerSession
from .user_pane import UserPane
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
        settings_manager: SettingsManager,
        event_bus: EventBus,
        system_stats: SystemStatsService | None = None,
    ) -> None:
        super().__init__(
            shell_window,
            window_name="shell-app-launcher",
            title=TITLE_APP_LAUNCHER,
            namespace="shell-app-launcher",
            placeholder="Buscar aplicaciones...",
            empty_text="Sin resultados",
            session=PickerSession(columns=1),
            layout="control_center",
            card_width=CONTROL_CENTER_WIDTH,
            card_height=CONTROL_CENTER_HEIGHT,
        )
        self._on_launch = on_launch
        self._on_new_instance = on_new_instance
        self._on_pin_toggle = on_pin_toggle
        self._on_favorite_toggle = on_favorite_toggle
        self._on_refresh = on_refresh
        self._settings_manager = settings_manager
        self._snapshot = ApplicationsSnapshot()
        self._rows: tuple[LauncherAppRow, ...] = ()
        self._rows_by_id: dict[str, LauncherAppRow] = {}
        self._row_versions: dict[str, tuple[bool, bool, str, str, str]] = {}
        self._mode: Destination = SEARCH_DESTINATION
        self._active_settings_category = CategoryId.GENERAL
        self._content_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._settings_scroll = Gtk.ScrolledWindow()
        self._settings_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._settings_scroll.get_style_context().add_class("settings-content-scroll")
        self._content_host.get_style_context().add_class("settings-content")
        self._settings_scroll.add(self._content_host)

        if self.left_slot is not None:
            user = UserPane(settings_manager, event_bus, system_stats=system_stats)
            self.left_slot.pack_start(user, True, True, 0)
        nav, self._nav_buttons = build_settings_nav(
            settings_manager,
            include_search=True,
            on_select=self._on_nav_selected,
        )
        if self.right_slot is not None:
            # Fill the full panel height so the category list is not compressed.
            self.right_slot.pack_start(nav, True, True, 0)

        self._list = Gtk.ListBox()
        self._list.set_name("launcher-app-list")
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
        self._list_overlay.get_style_context().add_class("launcher-list-overlay")
        self._list_overlay.add(scrolled)
        self._center_stack = Gtk.Stack()
        self._center_stack.get_style_context().add_class("launcher-center-stack")
        self._center_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._center_stack.set_transition_duration(100)
        self._center_stack.add_named(self._list_overlay, "launcher")
        self._center_stack.add_named(self._settings_scroll, "settings")
        self.content_box.pack_start(self._center_stack, True, True, 0)

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
        self.open_search_mode()

    def close_launcher(self) -> None:
        self._dismiss_action_menu()
        self.close_picker()

    def toggle_launcher(self) -> None:
        if self.is_effectively_open():
            self.close_picker()
            return
        self.open_search_mode()

    def warm_up(self) -> None:
        self._snapshot = self._on_refresh()
        self._rebuild_rows()
        super().warm_up()

    def open_search_mode(self) -> None:
        self._mode = SEARCH_DESTINATION
        self._update_mode_ui()
        self.open_picker(focus_search=True, reset_query=True)
        GLib.idle_add(self._refresh_after_present)

    def open_settings_mode(self, category: CategoryId = CategoryId.GENERAL) -> None:
        self._active_settings_category = category
        self._mode = category
        # Switch chrome + shell size before the door measures, and mount the
        # settings page immediately so search layout never flashes underneath.
        self._update_mode_ui()
        self._show_settings_category(category)
        if self.get_visible():
            return
        self.open_picker(focus_search=False, reset_query=False)

    def set_snapshot(self, snapshot: ApplicationsSnapshot) -> None:
        self._snapshot = snapshot
        if self.get_visible():
            if self._mode == SEARCH_DESTINATION:
                self._rebuild_rows(keep_selection=True)

    def on_prepare_open(self) -> None:
        # Refresh is deferred right after present for faster perceived open.
        return

    def on_after_show_all(self) -> None:
        # show_all() forces both Stack pages (and the search row) visible again.
        self._reassert_mode_visibility()

    def on_query_changed(self, query: str) -> None:
        if self._mode == SEARCH_DESTINATION:
            self._rebuild_rows()

    def on_activate(self) -> None:
        if self._mode == SEARCH_DESTINATION:
            self._launch_selected()

    def on_selection_moved(self) -> None:
        if self._mode != SEARCH_DESTINATION:
            return
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
            favorite = self._snapshot.is_favorite(application.id)
            pinned = self._snapshot.is_pinned(application.id)
            row = self._row_for(application, favorite=favorite, pinned=pinned)
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
        if key == Gdk.KEY_Escape and self._mode != SEARCH_DESTINATION:
            self._on_nav_selected(SEARCH_DESTINATION)
            return True
        if super_space:
            self.close_launcher()
            return True
        return super()._on_key_press(widget, event)

    def _row_for(
        self,
        application: DesktopApplication,
        *,
        favorite: bool,
        pinned: bool,
    ) -> LauncherAppRow:
        version = (
            favorite,
            pinned,
            application.name,
            application.icon,
            application.comment,
        )
        cached = self._rows_by_id.get(application.id)
        if cached is not None and self._row_versions.get(application.id) == version:
            return cached
        row = LauncherAppRow(
            application,
            favorite=favorite,
            pinned=pinned,
            on_open=self._open_application,
            on_new_instance=self._new_instance_application,
            on_favorite_toggle=self._on_favorite_toggle,
            on_pin_toggle=self._on_pin_toggle,
            on_context_menu=self._show_row_menu,
        )
        self._rows_by_id[application.id] = row
        self._row_versions[application.id] = version
        return row

    def _on_nav_selected(self, destination: Destination) -> None:
        self._mode = destination
        self._update_mode_ui()
        if destination == SEARCH_DESTINATION:
            self.on_query_changed(self._search.get_text())
            self.focus_search()
            return
        self._active_settings_category = destination
        self._show_settings_category(destination)

    def _update_mode_ui(self) -> None:
        search_mode = self._mode == SEARCH_DESTINATION
        self.set_search_row_visible(search_mode)
        if not search_mode:
            self.set_empty_visible(False)
        # While closed (or mid-open), skip crossfade so search does not flash
        # under settings during the door animation.
        if self.get_visible() and not self._opening:
            self._center_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self._center_stack.set_transition_duration(100)
        else:
            self._center_stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self._center_stack.set_transition_duration(0)
        self._reassert_mode_visibility()
        # Side rails stay fixed; only the center column (and total shell) grow for settings.
        center_width = (
            CONTROL_CENTER_CENTER_MIN_WIDTH if search_mode else CONTROL_CENTER_CENTER_SETTINGS_WIDTH
        )
        shell_width = CONTROL_CENTER_WIDTH if search_mode else CONTROL_CENTER_SETTINGS_WIDTH
        # Smooth width when switching modes on an already-open puerta; snap on first open.
        animate = self.get_visible() and not self._opening and not self._closing
        self.set_shell_size(
            shell_width,
            CONTROL_CENTER_HEIGHT,
            center_width=center_width,
            animate=animate,
        )
        for destination, button in self._nav_buttons.items():
            style = button.get_style_context()
            if destination == self._mode:
                style.add_class("active")
            else:
                style.remove_class("active")

    def _reassert_mode_visibility(self) -> None:
        """Keep only the active mode page visible (GTK3 show_all breaks Stack)."""
        search_mode = self._mode == SEARCH_DESTINATION
        target = "launcher" if search_mode else "settings"
        self._center_stack.set_visible_child_name(target)
        launcher_page = self._center_stack.get_child_by_name("launcher")
        settings_page = self._center_stack.get_child_by_name("settings")
        if launcher_page is not None:
            launcher_page.set_no_show_all(not search_mode)
            if search_mode:
                launcher_page.show()
            else:
                launcher_page.hide()
        if settings_page is not None:
            settings_page.set_no_show_all(search_mode)
            if search_mode:
                settings_page.hide()
            else:
                settings_page.show()
        self.set_search_row_visible(search_mode)
        if not search_mode:
            self.set_empty_visible(False)

    def _show_settings_category(self, category: CategoryId) -> None:
        show_settings_category(
            self._content_host,
            self._settings_manager,
            category,
            on_change=self._on_setting_changed,
        )

    def _on_setting_changed(self, key: str, value: object) -> None:
        self._settings_manager.set(key, value)
        if key.startswith("modo_noche.") or key.startswith("layout."):
            self._show_settings_category(self._active_settings_category)

    def _refresh_after_present(self) -> bool:
        self._snapshot = self._on_refresh()
        if self._mode == SEARCH_DESTINATION:
            self._rebuild_rows(keep_selection=True)
        return False
