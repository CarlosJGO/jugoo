"""Independent stacked-notifications window shown left of the notification popup."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ...config import (
    NOTIFICATION_POPUP_ICON_SIZE,
    NOTIFICATION_POPUP_LIST_SPACING,
    NOTIFICATION_POPUP_MAX_HEIGHT,
    NOTIFICATION_POPUP_OFFSET,
    NOTIFICATION_POPUP_WIDTH,
)
from ...models import NotificationSnapshot
from ...popup_handle import hide_popup, present_popup
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.notification_icon import apply_notification_icon
from ...window_identity import (
    TITLE_NOTIFICATION_GROUP,
    anchor_button_geometry,
    compute_popup_top_left,
    configure_interactive_popup,
    configure_toplevel,
    monitor_containing_point,
    popup_window_size,
    reposition_popup,
    register_shell_popup,
    schedule_popup_position,
)
from .notification_mini_row import NotificationMiniRow


class NotificationGroupWindow(Gtk.Window):
    """Independent toplevel window listing stacked notifications for one group."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        notification_service: NotificationService,
        group_snapshots: list[NotificationSnapshot],
        *,
        on_invoke_action: Callable[[int, str], None],
        on_dismiss: Callable[[int], None],
        on_open_app: Callable[[NotificationSnapshot], None],
        anchor: Gtk.Widget | None = None,
        popup_window: Gtk.Window | None = None,
        notifications_position: tuple[int, int, int] | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._service = notification_service
        self._group_snapshots = group_snapshots
        self._on_invoke_action = on_invoke_action
        self._on_dismiss = on_dismiss
        self._on_open_app = on_open_app
        self._fade_source_id = 0
        self._popup_window = popup_window
        self._anchor_widget = anchor
        self._notifications_position = notifications_position

        self.set_name("shell-notification-group-window")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATION_GROUP)
        configure_interactive_popup(self)
        window_height = NOTIFICATION_POPUP_MAX_HEIGHT + 16
        self.set_default_size(NOTIFICATION_POPUP_WIDTH, window_height)
        self.set_size_request(NOTIFICATION_POPUP_WIDTH, window_height)

        self.connect("focus-in-event", self._on_focus_in)
        self.connect("delete-event", self._on_delete)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("notification-group-window-content")
        outer.set_size_request(NOTIFICATION_POPUP_WIDTH, window_height)
        self.add(outer)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_size_request(NOTIFICATION_POPUP_WIDTH, NOTIFICATION_POPUP_MAX_HEIGHT)
        scrolled.get_style_context().add_class("notification-group-window-scroll")
        outer.pack_start(scrolled, True, True, 0)

        list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=NOTIFICATION_POPUP_LIST_SPACING,
        )
        list_box.get_style_context().add_class("notification-group-window-list")
        scrolled.add(list_box)

        for snapshot in group_snapshots:
            row = NotificationMiniRow(snapshot, on_dismiss=self._on_dismiss)
            row.connect("button-press-event", self._on_row_clicked, snapshot)
            list_box.pack_start(row, False, False, 0)

    def position_left_of_popup(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
    ) -> None:
        group_width = NOTIFICATION_POPUP_WIDTH
        group_height = NOTIFICATION_POPUP_MAX_HEIGHT + 16
        gap = 6

        parent_rect = self._resolve_parent_popup_rect(anchor, popup_window)
        if parent_rect is None:
            return
        notifications_left, notifications_top, notifications_width = parent_rect
        monitor = monitor_containing_point(notifications_left, notifications_top)
        if monitor is None:
            return

        left = notifications_left - gap - group_width
        notifications_right = notifications_left + notifications_width
        if left < monitor.x:
            left = notifications_right + gap
        top = notifications_top

        left = max(monitor.x, min(left, monitor.x + monitor.width - group_width))
        top = max(monitor.y, min(top, monitor.y + monitor.height - group_height))

        reposition_popup(self, title=TITLE_NOTIFICATION_GROUP, x=left, y=top)

    def _resolve_parent_popup_rect(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
    ) -> tuple[int, int, int] | None:
        if self._notifications_position is not None:
            return self._notifications_position

        geometry = anchor_button_geometry(anchor)
        if geometry is None:
            return None
        width, _height = popup_window_size(popup_window)
        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        left, top = compute_popup_top_left(
            button_center_x=geometry.center_x,
            button_bottom=geometry.bottom,
            popup_width=width,
            popup_height=NOTIFICATION_POPUP_MAX_HEIGHT,
            offset=NOTIFICATION_POPUP_OFFSET,
            monitor=monitor,
        )
        self._notifications_position = (left, top, width)
        return self._notifications_position

    def present_group(self) -> None:
        self.set_opacity(0.0)
        self.show_all()
        self.present()
        source_id = GLib.timeout_add(16, self._fade_in_tick)
        self._fade_source_id = source_id
        if self._popup_window is not None and self._anchor_widget is not None:
            if self.get_mapped():
                self.position_left_of_popup(self._anchor_widget, self._popup_window)
            else:
                self.connect("map-event", self._on_first_map)
            schedule_popup_position(
                lambda: self._position_from_anchor_after_popup_map()
            )

    def _position_from_anchor_after_popup_map(self) -> bool:
        if self._anchor_widget is not None and self._popup_window is not None:
            self.position_left_of_popup(self._anchor_widget, self._popup_window)
        return False

    def hide_group(self) -> None:
        if self._fade_source_id:
            GLib.source_remove(self._fade_source_id)
            self._fade_source_id = 0
        self.hide()
        self.set_opacity(1.0)

    def _on_first_map(self, _widget: Gtk.Widget, _event: Gdk.EventAny) -> bool:
        self.disconnect_by_func(self._on_first_map)
        self.position_left_of_popup(self._anchor_widget, self._popup_window)
        return False

    def _fade_in_tick(self) -> bool:
        next_opacity = min(1.0, self.get_opacity() + 0.20)
        self.set_opacity(next_opacity)
        if next_opacity >= 1.0:
            self._fade_source_id = 0
            return False
        return True

    def _on_focus_in(self, _widget: Gtk.Widget, _event: Gdk.EventFocus) -> bool:
        return False

    def _on_delete(self, _widget: Gtk.Widget, _event: Gdk.Event) -> bool:
        GLib.idle_add(self.hide_group)
        return True

    def _on_row_clicked(
        self,
        _widget: Gtk.Widget,
        event: Gdk.EventButton,
        snapshot: NotificationSnapshot,
    ) -> bool:
        if event.button != 1:
            return False
        default = next(
            (action.key for action in snapshot.actions if action.key == "default"),
            None,
        )
        if default is None and snapshot.actions:
            default = snapshot.actions[0].key
        if default is not None:
            self._on_invoke_action(snapshot.id, default)
        else:
            self._on_open_app(snapshot)
        GLib.idle_add(self.hide_group)
        return True
