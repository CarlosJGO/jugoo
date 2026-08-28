"""Independent stacked-notifications window shown left of the notification popup."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, Pango

from ...config import (
    NOTIFICATION_POPUP_ICON_SIZE,
    NOTIFICATION_POPUP_WIDTH,
    NOTIFICATION_POPUP_LIST_SPACING,
)
from ...models import NotificationSnapshot
from ...popup_handle import present_popup, hide_popup
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.notification_icon import apply_notification_icon
from ...window_identity import (
    TITLE_NOTIFICATIONS,
    anchor_button_geometry,
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
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._service = notification_service
        self._group_snapshots = group_snapshots
        self._on_invoke_action = on_invoke_action
        self._on_dismiss = on_dismiss
        self._on_open_app = on_open_app
        self._fade_source_id = 0

        self.set_name("shell-notification-group-window")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATIONS)
        configure_interactive_popup(self)
        self.set_default_size(NOTIFICATION_POPUP_WIDTH, 320)
        self.set_size_request(NOTIFICATION_POPUP_WIDTH, 240)

        self.connect("focus-out-event", self._on_focus_out)
        self.connect("focus-in-event", self._on_focus_in)
        self.connect("delete-event", self._on_delete)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_size_request(NOTIFICATION_POPUP_WIDTH, -1)
        outer.get_style_context().add_class("notification-group-window-content")
        self.add(outer)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.get_style_context().add_class("notification-group-window-scroll")
        outer.pack_start(scrolled, False, False, 0)

        list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=NOTIFICATION_POPUP_LIST_SPACING,
        )
        list_box.get_style_context().add_class("notification-group-window-list")
        scrolled.add(list_box)

        for snapshot in group_snapshots:
            row = NotificationMiniRow(snapshot)
            row.connect("button-press-event", self._on_row_clicked, snapshot)
            list_box.pack_start(row, False, False, 0)

    def position_left_of(self, anchor: Gtk.Widget, popup_window: Gtk.Window) -> None:
        geometry = anchor_button_geometry(anchor)
        if geometry is None:
            return

        popup_width, popup_height = popup_window_size(popup_window)
        group_width, group_height = popup_window_size(self)
        if group_width <= 1:
            group_width = NOTIFICATION_POPUP_WIDTH
        if group_height <= 1:
            group_height = 300

        margin = 8
        left = geometry.left - group_width - margin
        top = geometry.top

        if left < geometry.left - margin:
            left = geometry.left - margin

        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        if monitor is not None:
            left = max(monitor.x, min(left, monitor.x + monitor.width - group_width))
            top = max(monitor.y, min(top, monitor.y + monitor.height - group_height))

        reposition_popup(self, title=TITLE_NOTIFICATIONS, x=int(left), y=int(top))

    def present_group(self) -> None:
        self.set_opacity(0.0)
        self.show_all()
        self.present()
        source_id = GLib.timeout_add(16, self._fade_in_tick)
        self._fade_source_id = source_id

    def hide_group(self) -> None:
        if self._fade_source_id:
            GLib.source_remove(self._fade_source_id)
            self._fade_source_id = 0
        self.hide()
        self.set_opacity(1.0)

    def _fade_in_tick(self) -> bool:
        next_opacity = min(1.0, self.get_opacity() + 0.20)
        self.set_opacity(next_opacity)
        if next_opacity >= 1.0:
            self._fade_source_id = 0
            return False
        return True

    def _on_focus_in(self, _widget: Gtk.Widget, _event: Gdk.EventFocus) -> bool:
        return False

    def _on_focus_out(self, _widget: Gtk.Widget, _event: Gdk.EventFocus) -> bool:
        if not self.get_visible():
            return False
        if pointer_inside_window(self):
            return False
        GLib.idle_add(self.hide_group)
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
        target = event.widget
        while target is not None and target != _widget:
            if isinstance(target, Gtk.Button):
                return False
            target = target.get_parent()
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


def pointer_inside_window(window: Gtk.Window) -> bool:
    if not window.get_mapped():
        return False
    gdk_window = window.get_window()
    if gdk_window is None:
        return False
    pointer = window.get_display().get_default_seat().get_pointer()
    if pointer is None:
        return False
    _, root_x, root_y = pointer.get_position()
    origin = gdk_window.get_origin()
    if len(origin) == 3:
        _, win_x, win_y = origin
    else:
        win_x, win_y = origin
    allocation = window.get_allocation()
    return (
        win_x <= root_x <= win_x + allocation.width
        and win_y <= root_y <= win_y + allocation.height
    )
