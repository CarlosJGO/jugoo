"""Fullscreen radial launcher layer; its geometry stays fixed during animation."""

from __future__ import annotations

import math
from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango, PangoCairo

from ...identity import TITLE_RADIAL_LAUNCHER
from ...models import DesktopApplication
from ...ui.door import symmetric_ease
from ...ui.theme import active_theme
from ...window_identity import (
    configure_interactive_popup,
    configure_toplevel,
    register_shell_popup,
)
from .radial_layout import RadialLayout, RadialPosition, radial_layout

_MIN_DURATION_MS = 140
_MAX_DURATION_MS = 240
_DEFAULT_DURATION_MS = 190
_ANIMATION_SCALE = 0.48
_CENTER_RADIUS = 36.0

AppSelection = Callable[[DesktopApplication], None]


def animation_duration_ms() -> int:
    theme = active_theme()
    if theme is None:
        return _DEFAULT_DURATION_MS
    if not theme.animation.enabled or theme.animation.duration <= 0:
        return 0
    return max(_MIN_DURATION_MS, min(_MAX_DURATION_MS, int(theme.animation.duration)))


def _rounded_rectangle(
    context: object,
    left: float,
    top: float,
    width: float,
    height: float,
    radius: float,
) -> None:
    radius = min(radius, width / 2, height / 2)
    context.new_sub_path()
    context.arc(left + width - radius, top + radius, radius, -math.pi / 2, 0)
    context.arc(left + width - radius, top + height - radius, radius, 0, math.pi / 2)
    context.arc(left + radius, top + height - radius, radius, math.pi / 2, math.pi)
    context.arc(left + radius, top + radius, radius, math.pi, math.pi * 1.5)
    context.close_path()


class RadialLauncherOverlay(Gtk.Window):
    """Interactive layer for one output, with a cursor-preserving radial origin."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_activate: AppSelection | None = None,
        on_new_instance: AppSelection | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._on_activate = on_activate
        self._on_new_instance = on_new_instance
        self._applications: tuple[DesktopApplication, ...] = ()
        self._pending_applications: tuple[DesktopApplication, ...] | None = None
        self._selected = 0
        self._selection_dispatched = False
        self._progress = 0.0
        self._anim_from = 0.0
        self._anim_to = 0.0
        self._anim_started_us = 0
        self._anim_duration_ms = 0
        self._tick_id = 0
        self._width = 0
        self._height = 0
        self._layout: RadialLayout | None = None
        self._icon_cache: dict[tuple[str, int, int], object | None] = {}
        self._icon_theme = Gtk.IconTheme.get_default()
        self._icon_theme_handler = 0

        self.set_name("shell-radial-launcher")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_RADIAL_LAUNCHER)
        configure_interactive_popup(self)
        self.set_resizable(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self._configure_layer_shell()

        self._canvas = Gtk.DrawingArea()
        self._canvas.set_name("shell-radial-launcher-canvas")
        self._canvas.get_style_context().add_class("radial-launcher-canvas")
        self._canvas.set_can_focus(True)
        self._canvas.set_hexpand(True)
        self._canvas.set_vexpand(True)
        self._canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self._canvas.connect("draw", self._draw)
        self._canvas.connect("size-allocate", self._on_size_allocate)
        self._canvas.connect("motion-notify-event", self._on_motion)
        self._canvas.connect("button-press-event", self._on_click)
        self.add(self._canvas)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)
        if self._icon_theme is not None:
            self._icon_theme_handler = self._icon_theme.connect(
                "changed",
                self._on_icon_theme_changed,
            )

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def animating(self) -> bool:
        return self._tick_id != 0

    @property
    def layout(self) -> RadialLayout | None:
        return self._layout

    @property
    def applications(self) -> tuple[DesktopApplication, ...]:
        return self._applications

    def set_applications(self, applications: tuple[DesktopApplication, ...]) -> None:
        applications = tuple(applications)
        if self.get_visible() and self._progress > 0:
            self._pending_applications = applications
            return
        self._apply_applications(applications)

    def _apply_applications(self, applications: tuple[DesktopApplication, ...]) -> None:
        selected_id = (
            self._applications[self._selected].id
            if 0 <= self._selected < len(self._applications)
            else None
        )
        self._applications = applications
        self._selected = next(
            (index for index, app in enumerate(self._applications) if app.id == selected_id),
            min(self._selected, max(0, len(self._applications) - 1)),
        )
        self._refresh_layout()
        self._canvas.queue_draw()

    def open_on_monitor(
        self,
        monitor: Gdk.Monitor,
        *,
        origin: tuple[float, float] | None = None,
    ) -> None:
        """Show on ``monitor``; ``origin`` is monitor-local logical geometry."""
        geometry = monitor.get_geometry()
        self._width, self._height = geometry.width, geometry.height
        if origin is None:
            origin = (self._width / 2, self._height / 2)
        self._set_origin(origin)
        GtkLayerShell.set_monitor(self, monitor)
        self.set_default_size(self._width, self._height)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        if not self.get_visible():
            self._progress = 0.0
            self._anim_to = 0.0
            self._selection_dispatched = False
            self.show_all()
            self.present()
            self._canvas.grab_focus()
        self._animate_to(1.0)

    def close_animated(self) -> None:
        if not self.get_visible():
            return
        self._animate_to(0.0)

    def dismiss_immediately(self) -> None:
        self._cancel_animation()
        self._progress = 0.0
        self._anim_to = 0.0
        self._apply_pending_applications()
        self._canvas.queue_draw()
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.hide()

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-radial-launcher")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)

    def _set_origin(self, origin: tuple[float, float]) -> None:
        if self._width <= 0 or self._height <= 0:
            return
        self._layout = radial_layout(
            len(self._applications),
            self._width,
            self._height,
            origin=origin,
        )

    def _refresh_layout(self) -> None:
        if self._width > 0 and self._height > 0 and self._layout is not None:
            self._set_origin((self._layout.origin_x, self._layout.origin_y))

    def _on_size_allocate(self, _canvas: Gtk.Widget, allocation: Gtk.Allocation) -> None:
        if allocation.width <= 0 or allocation.height <= 0:
            return
        if (allocation.width, allocation.height) == (self._width, self._height):
            return
        self._width, self._height = allocation.width, allocation.height
        origin = (
            (self._layout.origin_x, self._layout.origin_y)
            if self._layout is not None
            else (self._width / 2, self._height / 2)
        )
        self._set_origin(origin)

    def _animate_to(self, target: float) -> None:
        if self.animating and target == self._anim_to:
            return
        self._cancel_animation()
        self._anim_from = self._progress
        self._anim_to = target
        self._anim_started_us = GLib.get_monotonic_time()
        self._anim_duration_ms = int(animation_duration_ms() * abs(target - self._progress))
        if self._anim_duration_ms <= 0 or self._progress == target:
            self._progress = target
            self._finish_animation()
            self._canvas.queue_draw()
            return
        self._tick_id = self._canvas.add_tick_callback(self._on_animation_tick)

    def _cancel_animation(self) -> None:
        if self._tick_id:
            self._canvas.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _on_animation_tick(self, _canvas: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        elapsed_ms = (GLib.get_monotonic_time() - self._anim_started_us) / 1000.0
        t = min(1.0, elapsed_ms / max(1, self._anim_duration_ms))
        eased = symmetric_ease(t)
        self._progress = self._anim_from + (self._anim_to - self._anim_from) * eased
        self._canvas.queue_draw()
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        self._progress = self._anim_to
        self._finish_animation()
        return GLib.SOURCE_REMOVE

    def _finish_animation(self) -> None:
        if self._anim_to != 0.0:
            return
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self._apply_pending_applications()
        self.hide()

    def _apply_pending_applications(self) -> None:
        if self._pending_applications is None:
            return
        applications = self._pending_applications
        self._pending_applications = None
        self._apply_applications(applications)

    def _lookup_color(
        self,
        name: str,
        fallback: Gdk.RGBA,
    ) -> Gdk.RGBA:
        style = self._canvas.get_style_context()
        found, color = style.lookup_color(name)
        return color if found else fallback

    def _draw(self, _canvas: Gtk.Widget, context: object) -> bool:
        width = self._canvas.get_allocated_width()
        height = self._canvas.get_allocated_height()
        if width <= 0 or height <= 0:
            return False
        if self._layout is None:
            self._layout = radial_layout(
                len(self._applications),
                width,
                height,
            )

        style = self._canvas.get_style_context()
        text = style.get_color(style.get_state())
        accent = self._lookup_color("shell_accent", text)
        on_accent = self._lookup_color("shell_on_accent", text)
        surface = self._lookup_color("shell_surface_bg_elevated", text)
        secondary = self._lookup_color("shell_text_secondary", text)
        border = self._lookup_color("shell_border_medium", text)
        backdrop = self._lookup_color("shell_backdrop", text)
        layout = self._layout
        progress = self._progress
        center_x, center_y = layout.center_x, layout.center_y

        context.set_source_rgba(
            backdrop.red,
            backdrop.green,
            backdrop.blue,
            backdrop.alpha * 0.22 * progress,
        )
        context.paint()

        for index, (application, position) in enumerate(
            zip(self._applications, layout.positions)
        ):
            self._draw_sector(
                context,
                application,
                position,
                index == self._selected,
                accent,
                surface,
                border,
            )

        animated_center_x = layout.origin_x + (center_x - layout.origin_x) * progress
        animated_center_y = layout.origin_y + (center_y - layout.origin_y) * progress
        center_radius = _CENTER_RADIUS * progress
        context.arc(animated_center_x, animated_center_y, center_radius, 0, math.tau)
        context.set_source_rgba(0, 0, 0, 0.24 * progress)
        context.fill()
        context.arc(animated_center_x, animated_center_y, center_radius, 0, math.tau)
        context.set_source_rgba(surface.red, surface.green, surface.blue, surface.alpha * progress)
        context.fill_preserve()
        context.set_source_rgba(accent.red, accent.green, accent.blue, 0.72 * progress)
        context.set_line_width(1.5)
        context.stroke()
        self._draw_center_icon(
            context,
            animated_center_x,
            animated_center_y,
            accent,
            progress,
        )
        if self._applications:
            selected = self._applications[self._selected]
            self._draw_label(
                context,
                selected.name,
                animated_center_x,
                animated_center_y + 19 * progress,
                64 * progress,
                8.5 * progress,
                text,
                progress,
            )
        if not self._applications:
            self._draw_empty_state(context, layout, secondary, surface, border, progress)
        return False

    def _draw_sector(
        self,
        context: object,
        application: DesktopApplication,
        position: RadialPosition,
        selected: bool,
        accent: Gdk.RGBA,
        surface: Gdk.RGBA,
        border: Gdk.RGBA,
    ) -> None:
        layout = self._layout
        if layout is None:
            return
        center_x = layout.origin_x + (
            layout.center_x - layout.origin_x
        ) * self._progress
        center_y = layout.origin_y + (
            layout.center_y - layout.origin_y
        ) * self._progress
        scale = _ANIMATION_SCALE + (1.0 - _ANIMATION_SCALE) * self._progress
        alpha = self._progress
        inner_radius = position.inner_radius * scale
        outer_radius = position.outer_radius * scale
        start_angle, end_angle = position.start_angle, position.end_angle
        context.new_sub_path()
        context.arc(center_x, center_y, outer_radius, start_angle, end_angle)
        if inner_radius > 0:
            context.arc_negative(center_x, center_y, inner_radius, end_angle, start_angle)
        else:
            context.line_to(center_x, center_y)
        context.close_path()
        if selected:
            context.set_source_rgba(
                accent.red,
                accent.green,
                accent.blue,
                0.25 * alpha,
            )
        else:
            context.set_source_rgba(
                surface.red,
                surface.green,
                surface.blue,
                surface.alpha * alpha,
            )
        context.fill_preserve()
        context.set_source_rgba(
            accent.red if selected else border.red,
            accent.green if selected else border.green,
            accent.blue if selected else border.blue,
            (0.9 if selected else 0.72) * alpha,
        )
        context.set_line_width(1.5 if selected else 1.0)
        context.stroke()

        icon_center_x = layout.origin_x + (
            position.center_x - layout.origin_x
        ) * self._progress
        icon_center_y = layout.origin_y + (
            position.center_y - layout.origin_y
        ) * self._progress
        icon_size = max(1, int(position.size * scale))
        icon = self._load_icon(application.icon, icon_size)
        if icon is not None:
            icon_x = icon_center_x - icon_size / 2
            icon_y = icon_center_y - icon_size / 2
            context.save()
            context.translate(icon_x, icon_y)
            context.scale(icon_size / icon.get_width(), icon_size / icon.get_height())
            Gdk.cairo_set_source_pixbuf(context, icon, 0, 0)
            context.paint_with_alpha(alpha)
            context.restore()

    def _draw_center_icon(
        self,
        context: object,
        center_x: float,
        center_y: float,
        accent: Gdk.RGBA,
        alpha: float,
    ) -> None:
        if self._applications:
            icon = self._load_icon("view-app-grid-symbolic", 18)
            if icon is not None:
                context.save()
                context.translate(center_x - 9, center_y - 14)
                context.scale(18 / icon.get_width(), 18 / icon.get_height())
                Gdk.cairo_set_source_pixbuf(context, icon, 0, 0)
                context.paint_with_alpha(alpha)
                context.restore()
                return
        context.set_source_rgba(accent.red, accent.green, accent.blue, alpha)
        context.arc(center_x - 5, center_y, 2.2, 0, math.tau)
        context.arc(center_x, center_y, 2.2, 0, math.tau)
        context.arc(center_x + 5, center_y, 2.2, 0, math.tau)
        context.fill()

    def _draw_empty_state(
        self,
        context: object,
        layout: RadialLayout,
        text: Gdk.RGBA,
        surface: Gdk.RGBA,
        border: Gdk.RGBA,
        progress: float,
    ) -> None:
        width = min(180.0, layout.half_width * 2) * progress
        center_x = layout.origin_x + (layout.center_x - layout.origin_x) * progress
        center_y = layout.origin_y + (layout.center_y - layout.origin_y) * progress
        top = center_y - 15 * progress
        height = 30 * progress
        _rounded_rectangle(
            context,
            center_x - width / 2,
            top,
            width,
            height,
            min(14, height / 2),
        )
        context.set_source_rgba(surface.red, surface.green, surface.blue, surface.alpha * progress)
        context.fill_preserve()
        context.set_source_rgba(border.red, border.green, border.blue, border.alpha * progress)
        context.set_line_width(1)
        context.stroke()
        self._draw_label(
            context,
            "Sin aplicaciones fijadas",
            center_x,
            top + height / 2,
            width - 16,
            11 * progress,
            text,
            progress,
        )

    def _draw_label(
        self,
        context: object,
        value: str,
        center_x: float,
        center_y: float,
        width: float,
        font_size: float,
        color: Gdk.RGBA,
        alpha: float,
    ) -> None:
        if alpha <= 0 or width <= 0:
            return
        layout = self._canvas.create_pango_layout(value)
        description = self._canvas.get_pango_context().get_font_description().copy()
        description.set_absolute_size(max(7.0, font_size) * Pango.SCALE)
        layout.set_font_description(description)
        layout.set_width(max(1, int(width * Pango.SCALE)))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(Pango.Alignment.CENTER)
        _ink, logical = layout.get_pixel_extents()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha * alpha)
        context.move_to(center_x - logical.width / 2, center_y - logical.height / 2)
        PangoCairo.show_layout(context, layout)

    def _load_icon(self, name: str, size: int):
        scale = max(1, self._canvas.get_scale_factor())
        key = (name, size, scale)
        if key in self._icon_cache:
            return self._icon_cache[key]
        theme = self._icon_theme
        if theme is None:
            return None
        for candidate in (name, "application-x-executable"):
            info = theme.lookup_icon_for_scale(
                candidate,
                size,
                scale,
                Gtk.IconLookupFlags.FORCE_SIZE,
            )
            if info is None:
                continue
            try:
                icon = info.load_icon()
            except GLib.Error as error:
                print(f"shell: radial launcher: could not load icon {candidate}: {error.message}")
                continue
            self._icon_cache[key] = icon
            return icon
        self._icon_cache[key] = None
        return None

    def _hit_test(self, x: float, y: float) -> int | None:
        layout = self._layout
        if layout is None:
            return None
        animated_center_x = layout.origin_x + (
            layout.center_x - layout.origin_x
        ) * self._progress
        animated_center_y = layout.origin_y + (
            layout.center_y - layout.origin_y
        ) * self._progress
        delta_x, delta_y = x - animated_center_x, y - animated_center_y
        radius = math.hypot(delta_x, delta_y)
        angle = math.atan2(delta_y, delta_x)
        scale = _ANIMATION_SCALE + (1.0 - _ANIMATION_SCALE) * self._progress
        if radius <= _CENTER_RADIUS * self._progress:
            return None
        for index, position in enumerate(layout.positions):
            angle_delta = math.atan2(
                math.sin(angle - (position.start_angle + position.end_angle) / 2),
                math.cos(angle - (position.start_angle + position.end_angle) / 2),
            )
            if (
                radius <= position.outer_radius * scale
                and abs(angle_delta)
                <= (position.end_angle - position.start_angle) / 2
            ):
                return index
        return None

    def _on_motion(self, _canvas: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        index = self._hit_test(event.x, event.y)
        if index is not None and index != self._selected:
            self._selected = index
            self._canvas.queue_draw()
        return False

    def _on_click(self, _canvas: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button not in (1, 3):
            return False
        index = self._hit_test(event.x, event.y)
        if index is None:
            if event.button == 1:
                self.close_animated()
                return True
            return False
        self._selected = index
        if event.button == 1:
            self._activate_selected()
        elif self._on_new_instance is not None:
            self._new_instance_selected()
        return True

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.close_animated()
            return True
        if event.keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self._move_selection(1)
            return True
        if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self._move_selection(-1)
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._applications:
            self._activate_selected()
            return True
        return False

    def _activate_selected(self) -> None:
        self._dispatch_selected(self._on_activate)

    def _new_instance_selected(self) -> None:
        self._dispatch_selected(self._on_new_instance)

    def _dispatch_selected(self, callback: AppSelection | None) -> None:
        if self._selection_dispatched or not self._applications:
            return
        self._selection_dispatched = True
        if callback is not None:
            callback(self._applications[self._selected])
        if self.get_visible() and self._anim_to != 0.0:
            self.close_animated()

    def _move_selection(self, step: int) -> None:
        if not self._applications:
            return
        self._selected = (self._selected + step) % len(self._applications)
        self._canvas.queue_draw()

    def _on_delete(self, *_args) -> bool:
        self.close_animated()
        return True

    def _on_destroy(self, *_args) -> None:
        self._cancel_animation()
        if self._icon_theme is not None and self._icon_theme_handler:
            self._icon_theme.disconnect(self._icon_theme_handler)
            self._icon_theme_handler = 0

    def _on_icon_theme_changed(self, *_args) -> None:
        self._icon_cache.clear()
        self._canvas.queue_draw()
