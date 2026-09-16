"""Shared GtkLayerShell surface hosting incoming notification toasts."""

from __future__ import annotations

from typing import Literal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gtk, GtkLayerShell, GLib

from ...config import (
    NOTIFICATION_POPUP_OFFSET,
    NOTIFICATIONS_TOAST_WIDTH,
    NOTIFICATIONS_WHISPER_TOP_MARGIN,
    NOTIFICATIONS_WHISPER_WIDTH,
    POPUP_EDGE_MARGIN,
)
from ...ui.theme import active_theme
from ...window_identity import (
    TITLE_NOTIFICATION_TOAST,
    anchor_button_geometry,
    compute_popup_top_left,
    configure_osd_window,
    configure_toplevel,
    monitor_containing_point,
    popup_window_size,
    query_hyprland_monitors,
    register_shell_popup,
    schedule_popup_position,
)

ToastPlacement = Literal["bell", "whisper"]

_FADE_TICK_MS = 16


def _fade_step() -> float:
    theme = active_theme()
    if theme is None:
        return 0.20
    if not theme.animation.enabled or theme.animation.duration <= _FADE_TICK_MS:
        return 1.0
    return min(1.0, _FADE_TICK_MS / theme.animation.duration)


class NotificationToastLayer(Gtk.Window):
    """One non-focusable overlay surface containing all visible toasts."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        toasts: list[Gtk.Widget] | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._shell_window = shell_window
        self._anchor_button: Gtk.Widget | None = None
        self._placement: ToastPlacement = "bell"
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

    @property
    def placement(self) -> ToastPlacement:
        return self._placement

    def set_placement(self, placement: ToastPlacement) -> None:
        """Switch between bell-anchored cards and top-center whisper chips."""
        if placement == self._placement:
            return
        self._placement = placement
        style = self._box.get_style_context()
        if placement == "whisper":
            self._box.set_spacing(4)
            self._box.set_size_request(NOTIFICATIONS_WHISPER_WIDTH, -1)
            self.set_default_size(NOTIFICATIONS_WHISPER_WIDTH, -1)
            style.add_class("notification-toast-container-whisper")
        else:
            self._box.set_spacing(8)
            self._box.set_size_request(NOTIFICATIONS_TOAST_WIDTH, -1)
            self.set_default_size(NOTIFICATIONS_TOAST_WIDTH, -1)
            style.remove_class("notification-toast-container-whisper")
        self.resize(1, 1)
        if self.get_visible():
            self.refresh_position()

    def add_toast(self, toast: Gtk.Widget) -> None:
        """Add a toast card to the dynamic vertical box."""
        if toast.get_parent() is None:
            self._box.pack_start(toast, False, False, 0)
        toast.show_all()
        self.resize(1, 1)
        self.refresh_position()

    def remove_toast(self, toast: Gtk.Widget) -> None:
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
        if self._placement == "whisper":
            self._pin_to_shell_monitor()
        else:
            anchor_window = anchor_button.get_window()
            if anchor_window is not None:
                output = anchor_button.get_display().get_monitor_at_window(anchor_window)
                if output is not None:
                    GtkLayerShell.set_monitor(self, output)
        if self.get_visible():
            self._position_later()
            return
        self._cancel_fade()
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
            self.set_opacity(1.0)
            self.show()
            self._box.show()
            self._position_later()
            return
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
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
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

    def _pin_to_shell_monitor(self) -> None:
        window = self._shell_window.get_window()
        display = self._shell_window.get_display()
        if window is not None and display is not None:
            output = display.get_monitor_at_window(window)
            if output is not None:
                GtkLayerShell.set_monitor(self, output)

    def _position_later(self) -> None:
        schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._placement == "whisper":
            return self._position_whisper()
        return self._position_bell_anchored()

    def _position_bell_anchored(self) -> bool:
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

    def _position_whisper(self) -> bool:
        width, _height = popup_window_size(self)
        if width <= 1:
            width = NOTIFICATIONS_WHISPER_WIDTH

        monitor = self._shell_monitor_rect()
        edge = POPUP_EDGE_MARGIN
        if monitor is None:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, edge)
            GtkLayerShell.set_margin(
                self, GtkLayerShell.Edge.TOP, NOTIFICATIONS_WHISPER_TOP_MARGIN
            )
            return False

        left = monitor.x + (monitor.width - width) // 2
        left = max(
            monitor.x + edge,
            min(left, monitor.x + monitor.width - width - edge),
        )
        top = monitor.y + NOTIFICATIONS_WHISPER_TOP_MARGIN
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, max(0, left - monitor.x))
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, max(edge, top - monitor.y))
        return False

    def _shell_monitor_rect(self):
        window = self._shell_window.get_window()
        if window is not None:
            origin = window.get_origin()
            if isinstance(origin, tuple) and len(origin) == 3:
                _ok, ox, oy = origin
            elif isinstance(origin, tuple) and len(origin) == 2:
                ox, oy = origin
            else:
                ox = oy = 0
            allocation = self._shell_window.get_allocation()
            cx = int(ox) + max(allocation.width, 1) // 2
            cy = int(oy) + max(allocation.height, 1) // 2
            found = monitor_containing_point(cx, cy)
            if found is not None:
                return found

        monitors = query_hyprland_monitors()
        return monitors[0] if monitors else None

    def _fade_in_tick(self) -> bool:
        opacity = min(1.0, self.get_opacity() + _fade_step())
        self.set_opacity(opacity)
        if opacity >= 1.0:
            self._fade_source_id = 0
            return False
        return True

    def _fade_out_tick(self) -> bool:
        opacity = max(0.0, self.get_opacity() - _fade_step())
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
