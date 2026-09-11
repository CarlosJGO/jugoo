"""Deep-space starfield painted behind shell surfaces (bar, popups, layers)."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Sequence

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import GLib, Gtk

from ..eventbus import EventBus
from .theme import THEME_CHANGED, Theme, active_theme, color_to_rgb

_TICK_MS = 48
_BASE_DENSITY = 0.045

CornerRadii = float | tuple[float, float, float, float]


@dataclass(frozen=True)
class _Star:
    x: float
    y: float
    radius: float
    base: float
    amplitude: float
    speed: float
    phase: float
    cool: float


def resolve_event_bus(widget: Gtk.Widget | None) -> EventBus | None:
    """Find the shell EventBus from a window or nested widget."""
    current: Gtk.Widget | None = widget
    while current is not None:
        bus = getattr(current, "event_bus", None)
        if isinstance(bus, EventBus):
            return bus
        bus = getattr(current, "_event_bus", None)
        if isinstance(bus, EventBus):
            return bus
        shell = getattr(current, "_shell_window", None)
        if shell is not None and shell is not current:
            nested = resolve_event_bus(shell)
            if nested is not None:
                return nested
        parent = current.get_parent()
        if parent is None and isinstance(current, Gtk.Window):
            break
        current = parent
    return None


def install_starfield(
    parent: Gtk.Container,
    child: Gtk.Widget,
    event_bus: EventBus | None = None,
    *,
    corner_radius: CornerRadii = 16.0,
    draw_rim: bool = False,
    glass: bool = True,
) -> "StarfieldBackground":
    """Wrap ``child`` in a starfield host and attach it to ``parent``."""
    host = StarfieldBackground(
        event_bus if event_bus is not None else resolve_event_bus(parent),
        corner_radius=corner_radius,
        draw_rim=draw_rim,
    )
    host.get_style_context().add_class("shell-starfield-host")
    if glass:
        child.get_style_context().add_class("shell-starfield-glass")
    host.add(child)
    if isinstance(parent, Gtk.Box):
        parent.pack_start(host, False, False, 0)
    else:
        parent.add(host)
    return host


class StarfieldBackground(Gtk.EventBox):
    """Cold void + twinkling stars; hosts layout/content as its child."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        *,
        corner_radius: CornerRadii = 0.0,
        draw_rim: bool = True,
    ) -> None:
        super().__init__()
        self._event_bus = event_bus
        self._corner_radii = _normalize_radii(corner_radius)
        self._draw_rim = draw_rim
        self._stars: tuple[_Star, ...] = ()
        self._layout_key = (0, 0)
        self._elapsed_ms = 0
        self._tick_id = 0
        self.set_app_paintable(True)
        self.set_above_child(False)
        self.set_visible_window(True)
        self.get_style_context().add_class("shell-starfield")
        self.connect("draw", self._on_draw)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)
        if event_bus is not None:
            event_bus.subscribe(THEME_CHANGED, self._on_theme_changed)

    def _on_theme_changed(self, _theme: object) -> None:
        self.queue_draw()

    def _on_map(self, *_args) -> None:
        self._ensure_tick()

    def _on_unmap(self, *_args) -> None:
        self._stop_tick()

    def _on_destroy(self, *_args) -> None:
        self._stop_tick()
        if self._event_bus is not None:
            self._event_bus.unsubscribe(THEME_CHANGED, self._on_theme_changed)

    def _ensure_tick(self) -> None:
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
            self._stop_tick()
            return
        if self._tick_id:
            return
        self._tick_id = GLib.timeout_add(_TICK_MS, self._on_tick)

    def _stop_tick(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def _on_tick(self) -> bool:
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
            self._tick_id = 0
            self.queue_draw()
            return False
        self._elapsed_ms += _TICK_MS
        self.queue_draw()
        return True

    def _on_size_allocate(self, _widget: Gtk.Widget, allocation) -> None:
        key = (max(0, int(allocation.width)), max(0, int(allocation.height)))
        if key == self._layout_key or key[0] < 8 or key[1] < 4:
            return
        self._layout_key = key
        self._stars = _generate_stars(key[0], key[1])
        self._ensure_tick()

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        width = float(self.get_allocated_width())
        height = float(self.get_allocated_height())
        if width <= 0 or height <= 0:
            return False

        theme = active_theme()
        void_rgb, mist_rgb = _space_palette(theme)

        _path_rounded_rect(cr, 0.0, 0.0, width, height, self._corner_radii)
        cr.clip()

        cr.set_source_rgb(*void_rgb)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        gradient = cairo.LinearGradient(0, 0, 0, height)
        gradient.add_color_stop_rgba(0.0, *void_rgb, 1.0)
        gradient.add_color_stop_rgba(0.55, *void_rgb, 1.0)
        gradient.add_color_stop_rgba(1.0, *mist_rgb, 0.55)
        cr.set_source(gradient)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        animate = theme is None or theme.animation.enabled
        t = self._elapsed_ms / 1000.0
        for star in self._stars:
            if animate:
                twinkle = star.base + star.amplitude * (
                    0.5 + 0.5 * math.sin(t * star.speed + star.phase)
                )
            else:
                twinkle = star.base
            alpha = max(0.08, min(1.0, twinkle))
            red = 0.92 - 0.18 * star.cool
            green = 0.94 - 0.08 * star.cool
            blue = 1.0
            if alpha > 0.75 and star.radius >= 1.15:
                cr.set_source_rgba(red, green, blue, alpha * 0.22)
                cr.arc(star.x, star.y, star.radius * 2.4, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgba(red, green, blue, alpha)
            cr.arc(star.x, star.y, star.radius, 0, 2 * math.pi)
            cr.fill()

        if self._draw_rim:
            # Hard edge on free sides (bottom + right) so the void doesn't look cropped.
            rim_r, rim_g, rim_b = _rim_rgb(theme)
            cr.set_line_width(2.0)
            cr.set_source_rgba(rim_r, rim_g, rim_b, 0.9)
            cr.move_to(0.0, height - 1.0)
            cr.line_to(width - 1.0, height - 1.0)
            cr.line_to(width - 1.0, 0.0)
            cr.stroke()

        # Return False so the child layout still paints above.
        return False


def _normalize_radii(value: CornerRadii) -> tuple[float, float, float, float]:
    if isinstance(value, (int, float)):
        radius = max(0.0, float(value))
        return (radius, radius, radius, radius)
    if len(value) != 4:
        raise ValueError("corner_radius tuple must be (tl, tr, br, bl)")
    return tuple(max(0.0, float(part)) for part in value)  # type: ignore[return-value]


def _path_rounded_rect(
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    radii: Sequence[float],
) -> None:
    tl, tr, br, bl = radii
    if tl <= 0 and tr <= 0 and br <= 0 and bl <= 0:
        cr.rectangle(x, y, width, height)
        return

    max_tl = min(tl, width / 2.0, height / 2.0)
    max_tr = min(tr, width / 2.0, height / 2.0)
    max_br = min(br, width / 2.0, height / 2.0)
    max_bl = min(bl, width / 2.0, height / 2.0)

    cr.new_sub_path()
    if max_tr > 0:
        cr.arc(x + width - max_tr, y + max_tr, max_tr, -math.pi / 2.0, 0.0)
    else:
        cr.move_to(x + width, y)
    if max_br > 0:
        cr.arc(x + width - max_br, y + height - max_br, max_br, 0.0, math.pi / 2.0)
    else:
        cr.line_to(x + width, y + height)
    if max_bl > 0:
        cr.arc(x + max_bl, y + height - max_bl, max_bl, math.pi / 2.0, math.pi)
    else:
        cr.line_to(x, y + height)
    if max_tl > 0:
        cr.arc(x + max_tl, y + max_tl, max_tl, math.pi, 3.0 * math.pi / 2.0)
    else:
        cr.line_to(x, y)
    cr.close_path()


def _space_palette(
    theme: Theme | None,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if theme is None:
        return (0.02, 0.03, 0.06), (0.05, 0.07, 0.12)
    void = color_to_rgb(theme.colors.background)
    primary = color_to_rgb(theme.colors.primary)
    mist = (
        void[0] * 0.72 + primary[0] * 0.08,
        void[1] * 0.72 + primary[1] * 0.08,
        void[2] * 0.78 + primary[2] * 0.12,
    )
    return void, mist


def _rim_rgb(theme: Theme | None) -> tuple[float, float, float]:
    if theme is None:
        return (0.32, 0.39, 0.55)
    return color_to_rgb(theme.colors.border_active)


def _generate_stars(width: int, height: int) -> tuple[_Star, ...]:
    count = max(18, int(width * _BASE_DENSITY * max(1.0, height / 28.0)))
    count = min(count, 280)
    rng = random.Random(0x5A1FE1D ^ (width * 73856093) ^ (height * 19349663))
    margin_x = 2.0
    margin_y = 1.5
    stars: list[_Star] = []
    for _ in range(count):
        kind = rng.random()
        if kind < 0.72:
            radius = rng.uniform(0.55, 0.95)
            base = rng.uniform(0.25, 0.55)
            amplitude = rng.uniform(0.12, 0.28)
        elif kind < 0.92:
            radius = rng.uniform(0.95, 1.35)
            base = rng.uniform(0.45, 0.7)
            amplitude = rng.uniform(0.18, 0.35)
        else:
            radius = rng.uniform(1.35, 1.85)
            base = rng.uniform(0.55, 0.82)
            amplitude = rng.uniform(0.2, 0.4)
        stars.append(
            _Star(
                x=rng.uniform(margin_x, max(margin_x + 1.0, width - margin_x)),
                y=rng.uniform(margin_y, max(margin_y + 1.0, height - margin_y)),
                radius=radius,
                base=base,
                amplitude=amplitude,
                speed=rng.uniform(0.7, 2.4),
                phase=rng.uniform(0.0, math.tau),
                cool=rng.uniform(0.0, 1.0),
            )
        )
    return tuple(stars)
