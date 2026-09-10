"""GtkLayerShell surface hosting the single Jugoo assistant card."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gtk, GtkLayerShell, GLib

from ...config import (
    ASSISTANT_CARD_WIDTH,
    ASSISTANT_SLIDE_PX,
    ASSISTANT_TOP_MARGIN,
    POPUP_EDGE_MARGIN,
)
from ...identity import TITLE_ASSISTANT
from ...ui.theme import active_theme
from ...window_identity import (
    configure_osd_window,
    configure_toplevel,
    monitor_containing_point,
    popup_window_size,
    query_hyprland_monitors,
    register_shell_popup,
    schedule_popup_position,
)
from .assistant_card import AssistantCard

_FADE_TICK_MS = 16


def _fade_step() -> float:
    theme = active_theme()
    if theme is None:
        return 0.20
    if not theme.animation.enabled or theme.animation.duration <= _FADE_TICK_MS:
        return 1.0
    return min(1.0, _FADE_TICK_MS / theme.animation.duration)


class AssistantLayer(Gtk.Window):
    """One non-focusable overlay for the assistant card (top-center, not bell-anchored)."""

    def __init__(self, shell_window: Gtk.Window) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._shell_window = shell_window
        self._fade_source_id = 0
        self._slide_source_id = 0
        self._target_top = ASSISTANT_TOP_MARGIN
        self._target_left = POPUP_EDGE_MARGIN
        self._card: AssistantCard | None = None

        self.set_name("shell-assistant-layer")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_ASSISTANT)
        configure_osd_window(self)
        self.set_default_size(ASSISTANT_CARD_WIDTH, -1)
        self._configure_layer_shell()

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._box.set_size_request(ASSISTANT_CARD_WIDTH, -1)
        self._box.get_style_context().add_class("assistant-card-layer")
        self.add(self._box)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-assistant")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)

    def set_card(self, card: AssistantCard) -> None:
        """Attach the single card (replacing any previous child)."""
        for child in list(self._box.get_children()):
            self._box.remove(child)
        self._card = card
        if card.get_parent() is None:
            self._box.pack_start(card, False, False, 0)
        card.show_all()
        self.resize(1, 1)

    def clear_card(self) -> None:
        for child in list(self._box.get_children()):
            self._box.remove(child)
        self._card = None
        self.resize(1, 1)

    def show_layer(self) -> None:
        self._pin_to_shell_monitor()
        self._cancel_fade()
        self._cancel_slide()
        theme = active_theme()
        self._compute_target_position()
        if theme is not None and not theme.animation.enabled:
            self.set_opacity(1.0)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, self._target_left)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self._target_top)
            self.show()
            self._box.show()
            self._position_later()
            return

        start_top = max(0, self._target_top - ASSISTANT_SLIDE_PX)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, self._target_left)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, start_top)
        self.set_opacity(0.0)
        self.show()
        self._box.show()
        self._position_later()
        self._fade_source_id = GLib.timeout_add(_FADE_TICK_MS, self._fade_in_tick)
        self._slide_source_id = GLib.timeout_add(_FADE_TICK_MS, self._slide_in_tick)

    def hide_layer(self) -> None:
        self._cancel_fade()
        self._cancel_slide()
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
        self._cancel_slide()
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
        schedule_popup_position(self._reposition)

    def _reposition(self) -> bool:
        self._compute_target_position()
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, self._target_left)
        # Keep current top during fade-in slide; snap if fully visible.
        if self.get_opacity() >= 0.99:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self._target_top)
        return False

    def _compute_target_position(self) -> None:
        width, _height = popup_window_size(self)
        if width <= 1:
            width = ASSISTANT_CARD_WIDTH

        monitor = self._shell_monitor_rect()
        edge = POPUP_EDGE_MARGIN
        if monitor is None:
            self._target_left = edge
            self._target_top = ASSISTANT_TOP_MARGIN
            return

        # Top-center of the shell's monitor, with breathing room below the bar.
        left = monitor.x + (monitor.width - width) // 2
        left = max(monitor.x + edge, min(left, monitor.x + monitor.width - width - edge))
        top = monitor.y + ASSISTANT_TOP_MARGIN
        self._target_left = max(0, left - monitor.x)
        self._target_top = max(edge, top - monitor.y)

    def _shell_monitor_rect(self):
        window = self._shell_window.get_window()
        if window is not None:
            origin = window.get_origin()
            # get_origin returns (bool, x, y) on newer GI or (x, y) on older.
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

    def _slide_in_tick(self) -> bool:
        current = GtkLayerShell.get_margin(self, GtkLayerShell.Edge.TOP)
        step = max(1, int(ASSISTANT_SLIDE_PX * _fade_step() + 0.5))
        nxt = min(self._target_top, current + step)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, nxt)
        if nxt >= self._target_top:
            self._slide_source_id = 0
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

    def _cancel_slide(self) -> None:
        if self._slide_source_id:
            GLib.source_remove(self._slide_source_id)
            self._slide_source_id = 0
