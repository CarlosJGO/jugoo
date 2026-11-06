"""Visual prototype for the radial launcher proposed in task 7.1.

Run inside the Wayland session with:

    PYTHONPATH=. /usr/bin/python3 -m shell.prototypes.radial_launcher
    PYTHONPATH=. /usr/bin/python3 -m shell.prototypes.radial_launcher --demo-count 8
    PYTHONPATH=. /usr/bin/python3 -m shell.prototypes.radial_launcher --monitor 1

The default view reads the existing pinned-app preferences and desktop catalog,
but selecting an item only prints it; this prototype does not launch apps.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ..eventbus import EventBus
from ..icons import FALLBACK_ICON
from ..models import DesktopApplication
from ..servicios.aplicaciones.applications import ApplicationsService
from ..ui.door import symmetric_ease
from ..window_identity import configure_toplevel

_FRAME_MS = 16
_NODE_MAX = 92
_NODE_MIN = 48
_NODE_GAP = 18
_DEMO_APPS = (
    ("Firefox", "firefox"),
    ("Archivos", "org.gnome.Nautilus"),
    ("Terminal", "utilities-terminal"),
    ("Editor", "accessories-text-editor"),
    ("Musica", "multimedia-player"),
    ("Steam", "steam"),
    ("Camara", "camera-photo"),
    ("Ajustes", "preferences-system"),
    ("Imagenes", "image-x-generic"),
    ("Video", "video-x-generic"),
    ("Calculadora", "accessories-calculator"),
    ("Web", "web-browser"),
    ("Notas", "accessories-text-editor"),
    ("Juegos", "applications-games"),
    ("Red", "network-workgroup"),
    ("Sistema", "applications-system"),
)


@dataclass(frozen=True)
class RadialPosition:
    center_x: float
    center_y: float
    size: float


def radial_positions(count: int, width: int, height: int) -> tuple[RadialPosition, ...]:
    """Place items on a centered ring, shrinking nodes before they can overlap."""
    if count <= 0 or width <= 0 or height <= 0:
        return ()

    center_x = width / 2
    center_y = height / 2
    max_radius = max(0.0, min(width, height) / 2 - _NODE_MAX / 2 - 24)
    if count == 1:
        radius = min(max_radius, min(width, height) * 0.30)
        return (RadialPosition(center_x, center_y - radius, _NODE_MAX),)

    half_angle = math.sin(math.pi / count)
    available_chord = max(0.0, 2 * max_radius * half_angle)
    node_size = min(
        _NODE_MAX,
        max(_NODE_MIN, (available_chord - _NODE_GAP) / math.sqrt(2)),
    )
    min_radius = (node_size * math.sqrt(2) + _NODE_GAP) / (2 * half_angle)
    radius = min(max_radius, max(min_radius, min(width, height) * 0.30))
    return tuple(
        RadialPosition(
            center_x + math.cos(-math.pi / 2 + math.tau * index / count) * radius,
            center_y + math.sin(-math.pi / 2 + math.tau * index / count) * radius,
            node_size,
        )
        for index in range(count)
    )


def verify_layout() -> None:
    """Cheap geometry check for common counts and varied output sizes."""
    for width, height in ((1920, 1080), (1366, 768), (1280, 1024), (800, 600)):
        for count in (1, 2, 3, 4, 6, 8, 12, 16):
            positions = radial_positions(count, width, height)
            assert len(positions) == count
            for position in positions:
                half = position.size / 2
                assert half <= position.center_x <= width - half
                assert half <= position.center_y <= height - half
            for index, left in enumerate(positions):
                for right in positions[index + 1 :]:
                    delta_x = abs(right.center_x - left.center_x)
                    delta_y = abs(right.center_y - left.center_y)
                    distance = math.hypot(delta_x, delta_y)
                    assert distance + 1e-6 >= (left.size + right.size) / 2
                    assert not (
                        delta_x < (left.size + right.size) / 2
                        and delta_y < (left.size + right.size) / 2
                    )
    print("radial layout: counts 1-16 fit without overlap on 4 output sizes")


def _rounded_rectangle(
    context,
    left: float,
    top: float,
    width: float,
    height: float,
    radius: float,
) -> None:
    context.new_sub_path()
    context.arc(left + width - radius, top + radius, radius, -math.pi / 2, 0)
    context.arc(left + width - radius, top + height - radius, radius, 0, math.pi / 2)
    context.arc(left + radius, top + height - radius, radius, math.pi / 2, math.pi)
    context.arc(left + radius, top + radius, radius, math.pi, math.pi * 1.5)
    context.close_path()


class RadialPrototype:
    def __init__(
        self,
        applications: tuple[DesktopApplication, ...],
        monitor: Gdk.Monitor,
        *,
        duration_ms: int,
    ) -> None:
        geometry = monitor.get_geometry()
        self._applications = applications
        self._monitor = monitor
        self._duration_ms = max(0, duration_ms)
        self._progress = 0.0
        self._anim_from = 0.0
        self._anim_to = 1.0
        self._anim_started_us = 0
        self._anim_duration_ms = 0
        self._tick_id = 0
        self._selected = 0
        self._icon_cache: dict[tuple[str, int], object | None] = {}

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_name("shell-radial-prototype")
        configure_toplevel(self.window, title="Jugoo Radial Launcher Prototype")
        self.window.set_decorated(False)
        self.window.set_resizable(True)
        self.window.set_default_size(geometry.width, geometry.height)
        visual = self.window.get_screen().get_rgba_visual()
        if visual is not None:
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        self._configure_layer_shell()

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.canvas.connect("draw", self._draw)
        self.canvas.connect("button-press-event", self._on_click)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.window.add(self.canvas)
        self.window.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.window.connect("key-press-event", self._on_key_press)
        self.window.connect("delete-event", self._on_delete)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_namespace(self.window, "shell-radial-prototype")
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(self.window, -1)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_monitor(self.window, self._monitor)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self.window, edge, True)

    def run(self) -> None:
        self.window.show_all()
        self.window.present()
        self._animate_to(1.0)
        Gtk.main()

    def _animate_to(self, target: float) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._anim_from = self._progress
        self._anim_to = target
        distance = abs(self._anim_to - self._anim_from)
        self._anim_started_us = GLib.get_monotonic_time()
        self._anim_duration_ms = int(self._duration_ms * distance)
        if self._anim_duration_ms <= 0:
            self._progress = target
            self.canvas.queue_draw()
            self._finish_animation()
            return
        self._tick_id = GLib.timeout_add(_FRAME_MS, self._on_animation_tick)

    def _on_animation_tick(self) -> bool:
        elapsed = (GLib.get_monotonic_time() - self._anim_started_us) / 1000
        t = min(1.0, elapsed / max(1, self._anim_duration_ms))
        eased = symmetric_ease(t)
        self._progress = self._anim_from + (self._anim_to - self._anim_from) * eased
        self.canvas.queue_draw()
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        self._progress = self._anim_to
        self._finish_animation()
        return GLib.SOURCE_REMOVE

    def _finish_animation(self) -> None:
        if self._anim_to == 0.0:
            self.window.hide()
            Gtk.main_quit()

    def _draw(self, _canvas: Gtk.Widget, context) -> bool:
        allocation = self.canvas.get_allocation()
        width, height = allocation.width, allocation.height
        center_x, center_y = width / 2, height / 2
        positions = radial_positions(len(self._applications), width, height)
        progress = self._progress

        context.set_source_rgba(0.015, 0.025, 0.045, 0.76 * progress)
        context.paint()

        for position in positions:
            draw_x = center_x + (position.center_x - center_x) * progress
            draw_y = center_y + (position.center_y - center_y) * progress
            context.set_line_width(1.0)
            context.set_source_rgba(0.40, 0.70, 0.95, 0.24 * progress)
            context.move_to(center_x, center_y)
            context.line_to(draw_x, draw_y)
            context.stroke()

        for index, (application, position) in enumerate(zip(self._applications, positions)):
            self._draw_application(context, application, position, index == self._selected)

        center_radius = 48 * progress
        context.arc(center_x, center_y, center_radius, 0, math.tau)
        context.set_source_rgba(0.08, 0.14, 0.22, 0.96 * progress)
        context.fill_preserve()
        context.set_source_rgba(0.42, 0.76, 1.0, 0.72 * progress)
        context.set_line_width(1.5)
        context.stroke()
        context.select_font_face("Sans", 0, 0)
        context.set_font_size(13)
        label = "Jugoo"
        extents = context.text_extents(label)
        context.set_source_rgba(0.93, 0.96, 1.0, progress)
        context.move_to(center_x - extents.width / 2, center_y + extents.height / 2)
        context.show_text(label)
        if not self._applications:
            message = "No hay aplicaciones fijadas"
            context.set_font_size(14)
            extents = context.text_extents(message)
            context.set_source_rgba(0.78, 0.84, 0.91, progress)
            context.move_to(center_x - extents.width / 2, center_y + 78)
            context.show_text(message)
        return False

    def _draw_application(self, context, application: DesktopApplication, position: RadialPosition, selected: bool) -> None:
        center_x = self.canvas.get_allocated_width() / 2
        center_y = self.canvas.get_allocated_height() / 2
        draw_x = center_x + (position.center_x - center_x) * self._progress
        draw_y = center_y + (position.center_y - center_y) * self._progress
        scale = 0.42 + 0.58 * self._progress
        size = position.size * scale
        radius = size / 2
        alpha = self._progress
        left, top = draw_x - radius, draw_y - radius

        _rounded_rectangle(context, left, top, size, size, 14)
        if selected:
            context.set_source_rgba(0.10, 0.25, 0.38, 0.96 * alpha)
        else:
            context.set_source_rgba(0.07, 0.11, 0.17, 0.94 * alpha)
        context.fill_preserve()
        context.set_source_rgba(
            0.38 if selected else 0.45,
            0.77 if selected else 0.55,
            1.0 if selected else 0.68,
            (0.95 if selected else 0.28) * alpha,
        )
        context.set_line_width(2 if selected else 1)
        context.stroke()

        icon = self._load_icon(application.icon, int(size * 0.40))
        if icon is not None:
            icon_x = draw_x - icon.get_width() / 2
            icon_y = draw_y - radius * 0.54
            Gdk.cairo_set_source_pixbuf(context, icon, icon_x, icon_y)
            context.paint_with_alpha(alpha)

        name = application.name
        context.select_font_face("Sans", 0, 0)
        context.set_font_size(11 * scale)
        extents = context.text_extents(name)
        if extents.width > size - 10:
            name = name[: max(1, int(len(name) * (size - 14) / extents.width))] + "..."
            extents = context.text_extents(name)
        context.set_source_rgba(0.91, 0.94, 0.98, alpha)
        context.move_to(draw_x - extents.width / 2, draw_y + radius * 0.57)
        context.show_text(name)

    def _load_icon(self, name: str, size: int):
        key = (name, size)
        if key in self._icon_cache:
            return self._icon_cache[key]
        theme = Gtk.IconTheme.get_default()
        for candidate in (name, "application-x-executable"):
            try:
                info = theme.lookup_icon(candidate, size, Gtk.IconLookupFlags.FORCE_SIZE)
                if info is not None:
                    icon = info.load_icon()
                    self._icon_cache[key] = icon
                    return icon
            except Exception:
                continue
        self._icon_cache[key] = None
        return None

    def _hit_test(self, x: float, y: float) -> int | None:
        positions = radial_positions(
            len(self._applications),
            self.canvas.get_allocated_width(),
            self.canvas.get_allocated_height(),
        )
        for index, position in enumerate(positions):
            center_x = self.canvas.get_allocated_width() / 2
            center_y = self.canvas.get_allocated_height() / 2
            draw_x = center_x + (position.center_x - center_x) * self._progress
            draw_y = center_y + (position.center_y - center_y) * self._progress
            radius = position.size * (0.42 + 0.58 * self._progress) / 2
            if abs(x - draw_x) <= radius and abs(y - draw_y) <= radius:
                return index
        return None

    def _on_motion(self, _canvas: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        index = self._hit_test(event.x, event.y)
        if index is not None and index != self._selected:
            self._selected = index
            self.canvas.queue_draw()
        return False

    def _on_click(self, _canvas: Gtk.Widget, event: Gdk.EventButton) -> bool:
        index = self._hit_test(event.x, event.y)
        if index is None:
            self._animate_to(0.0)
            return True
        self._selected = index
        application = self._applications[index]
        print(f"prototype selected: {application.name} ({application.id})", flush=True)
        self._animate_to(0.0)
        return True

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._animate_to(0.0)
            return True
        if event.keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self._selected = (self._selected + 1) % max(1, len(self._applications))
            self.canvas.queue_draw()
            return True
        if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self._selected = (self._selected - 1) % max(1, len(self._applications))
            self.canvas.queue_draw()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._applications:
            application = self._applications[self._selected]
            print(f"prototype selected: {application.name} ({application.id})", flush=True)
            self._animate_to(0.0)
            return True
        return False

    def _on_delete(self, *_args) -> bool:
        self._animate_to(0.0)
        return True


def _demo_applications(count: int) -> tuple[DesktopApplication, ...]:
    return tuple(
        DesktopApplication(id=f"demo-{index}", name=name, icon=icon)
        for index, (name, icon) in enumerate(_DEMO_APPS[:count])
    )


def _pinned_applications() -> tuple[DesktopApplication, ...]:
    service = ApplicationsService(EventBus())
    service.start()
    snapshot = service.snapshot
    applications = tuple(
        snapshot.app_by_id(app_id)
        or DesktopApplication(id=app_id, name=app_id, icon=FALLBACK_ICON)
        for app_id in snapshot.pinned_ids
    )
    service.close()
    return applications


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jugoo radial launcher visual prototype")
    parser.add_argument("--monitor", type=int, default=0, help="GDK monitor index (default: 0)")
    parser.add_argument("--demo-count", type=int, choices=range(1, len(_DEMO_APPS) + 1))
    parser.add_argument("--duration", type=int, default=200, help="animation duration in ms")
    parser.add_argument("--no-animation", action="store_true")
    parser.add_argument("--verify-layout", action="store_true")
    args = parser.parse_args(argv)

    if args.verify_layout:
        verify_layout()
        return 0

    if not Gtk.init_check(None):
        print("radial prototype: no GTK display available", file=sys.stderr)
        return 1
    display = Gdk.Display.get_default()
    monitor_count = display.get_n_monitors() if display is not None else 0
    if not monitor_count or not 0 <= args.monitor < monitor_count:
        print(f"radial prototype: monitor index {args.monitor} is unavailable", file=sys.stderr)
        return 2

    monitor = display.get_monitor(args.monitor)
    applications = (
        _demo_applications(args.demo_count)
        if args.demo_count is not None
        else _pinned_applications()
    )
    if not applications:
        print("radial prototype: no pinned applications; use --demo-count 8", flush=True)

    duration_ms = 0 if args.no_animation else max(0, args.duration)
    prototype = RadialPrototype(applications, monitor, duration_ms=duration_ms)
    geometry = monitor.get_geometry()
    print(
        f"radial prototype: monitor {args.monitor} {geometry.width}x{geometry.height} "
        f"scale={monitor.get_scale_factor()} apps={len(applications)}; "
        "arrows move, Enter selects, Escape/outside click closes",
        flush=True,
    )
    prototype.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())