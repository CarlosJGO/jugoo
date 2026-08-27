"""Shared GtkLayerShell surface hosting incoming notification toasts."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gtk, GtkLayerShell, GLib

from ...config import (
    NOTIFICATION_POPUP_OFFSET,
    NOTIFICATIONS_TOAST_WIDTH,
)
from ...window_identity import (
    TITLE_NOTIFICATION_TOAST,
    anchor_button_geometry,
    compute_popup_top_left,
    configure_osd_window,
    configure_toplevel,
    monitor_containing_point,
    popup_window_size,
    register_shell_popup,
    schedule_popup_position,
)
from .notification_toast import NotificationToast

_FADE_TICK_MS = 16
_FADE_STEP = 0.20


class NotificationToastLayer(Gtk.Window):
    """One non-focusable overlay surface containing all visible toasts."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        toasts: list[NotificationToast] | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._anchor_button: Gtk.Widget | None = None
        self._fade_source_id = 0

        self.set_name("shell-notification-toast-layer")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATION_TOAST)
        configure_osd_window(self)
        self.set_default_size(NOTIFICATIONS_TOAST_WIDTH, -1)
        self._configure_layer_shell()

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._box.set_size_request(NOTIFICATIONS_TOAST_WIDTH, -1)
        self._box.get_style_context().add_class("notification-toast-container")
        self.add(self._box)

        if toasts:
            for toast in toasts:
                self.add_toast(toast)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-notification-toasts")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)

    def add_toast(self, toast: NotificationToast) -> None:
        """Add a toast card to the dynamic vertical box."""
        if toast.get_parent() is None:
            self._box.pack_start(toast, False, False, 0)
        toast.show_all()
        self.resize(1, 1)
        self.refresh_position()

    def remove_toast(self, toast: NotificationToast) -> None:
        """Remove a toast card from the dynamic vertical box."""
        if toast.get_parent() == self._box:
            self._box.remove(toast)
        self.resize(1, 1)
        self.refresh_position()

    def clear_toasts(self) -> None:
        """Remove all toasts from the container."""
        for child in self._box.get_children():
            self._box.remove(child)
        self.resize(1, 1)

    def show_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        anchor_window = anchor_button.get_window()
        if anchor_window is not None:
            output = anchor_button.get_display().get_monitor_at_window(anchor_window)
            if output is not None:
                GtkLayerShell.set_monitor(self, output)
        if self.get_visible():
            self._position_later()
            return
        self._cancel_fade()
        self.set_opacity(0.0)
        self.show()
        self._box.show()
        self._position_later()
        self._fade_source_id = GLib.timeout_add(_FADE_TICK_MS, self._fade_in_tick)

    def hide_layer(self) -> None:
        self._anchor_button = None
        self._cancel_fade()
        if not self.get_visible():
            self.hide()
            self.set_opacity(1.0)
            return
        self._fade_source_id = GLib.timeout_add(_FADE_TICK_MS, self._fade_out_tick)

    def destroy(self) -> None:
        self._cancel_fade()
        super().destroy()

    def refresh_position(self) -> None:
        if self.get_visible():
            self._position_later()

    def _position_later(self) -> None:
        schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        anchor = self._anchor_button
        if anchor is None:
            return False
        geometry = anchor_button_geometry(anchor)
        if geometry is None:
            return False

        width, height = popup_window_size(self)
        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        left, top = compute_popup_top_left(
            button_center_x=geometry.center_x,
            button_bottom=geometry.bottom,
            popup_width=width,
            popup_height=height,
            offset=NOTIFICATION_POPUP_OFFSET,
            monitor=monitor,
        )
        if monitor is not None:
            left -= monitor.x
            top -= monitor.y
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, max(0, left))
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, max(0, top))
        return False

    def _fade_in_tick(self) -> bool:
        opacity = min(1.0, self.get_opacity() + _FADE_STEP)
        self.set_opacity(opacity)
        if opacity >= 1.0:
            self._fade_source_id = 0
            return False
        return True

    def _fade_out_tick(self) -> bool:
        opacity = max(0.0, self.get_opacity() - _FADE_STEP)
        self.set_opacity(opacity)
        if opacity <= 0.02:
            self._fade_source_id = 0
            self.hide()
            self.set_opacity(1.0)
            return False
        return True

    def _cancel_fade(self) -> None:
        if self._fade_source_id:
            GLib.source_remove(self._fade_source_id)
            self._fade_source_id = 0
