"""Irregular asteroid silhouette + rocky surface render."""

from __future__ import annotations

import math
import random

import cairo
import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ..theme import active_theme, color_to_rgb
from .base import ContentInsets, add_disguise_classes
from .roles import DisguiseId
from .surface import ShapedDisguiseHost


class AsteroidDisguise:
    id = DisguiseId.ASTEROID

    def wrap(self, content: Gtk.Widget) -> Gtk.Widget:
        return ShapedDisguiseHost(self, content, animate=False)

    def style_widget(self, widget: Gtk.Widget) -> None:
        add_disguise_classes(widget, self.id, *self.style_classes())

    def style_classes(self) -> tuple[str, ...]:
        return ("disguise-asteroid-surface",)

    def build_shape(self, cr: cairo.Context, width: float, height: float) -> None:
        _asteroid_path(cr, width, height)

    def content_insets(self, width: float, height: float) -> ContentInsets:
        return ContentInsets(
            top=max(22, int(height * 0.10)),
            right=max(28, int(width * 0.10)),
            bottom=max(24, int(height * 0.11)),
            left=max(28, int(width * 0.10)),
        )

    def render(
        self,
        cr: cairo.Context,
        width: float,
        height: float,
        *,
        elapsed_ms: int = 0,
    ) -> None:
        rock, mid, lit, shade = _palette(active_theme())
        cr.set_source_rgb(*rock)
        cr.paint()

        light = cairo.LinearGradient(0, 0, width * 0.9, height)
        light.add_color_stop_rgba(0.0, lit[0], lit[1], lit[2], 0.5)
        light.add_color_stop_rgba(0.4, mid[0], mid[1], mid[2], 0.18)
        light.add_color_stop_rgba(1.0, shade[0], shade[1], shade[2], 0.7)
        cr.set_source(light)
        cr.paint()

        rng = random.Random(0xA57E401D ^ (int(width) << 9) ^ int(height))
        for _ in range(max(60, int(width * height / 900))):
            cr.set_source_rgba(lit[0], lit[1], lit[2], rng.uniform(0.04, 0.12))
            cr.rectangle(rng.uniform(0, width), rng.uniform(0, height), 1.3, 1.3)
            cr.fill()

        for _ in range(max(6, int(width / 70))):
            x0, y0 = rng.uniform(0, width), rng.uniform(0, height)
            x1, y1 = rng.uniform(0, width), rng.uniform(0, height)
            cr.set_source_rgba(0, 0, 0, rng.uniform(0.08, 0.18))
            cr.set_line_width(1.4)
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.stroke()

        crater_n = max(10, min(36, int(width * height / 5500)))
        for i in range(crater_n):
            if i < max(2, crater_n // 6):
                radius = rng.uniform(12.0, min(34.0, min(width, height) * 0.11))
            elif i < crater_n // 2:
                radius = rng.uniform(6.0, 12.0)
            else:
                radius = rng.uniform(2.5, 6.0)
            cx = rng.uniform(width * 0.14, width * 0.86)
            cy = rng.uniform(height * 0.14, height * 0.86)
            depth = rng.uniform(0.35, 1.0)
            cr.set_source_rgba(0, 0, 0, 0.2 + depth * 0.28)
            cr.arc(cx, cy, radius, 0, math.tau)
            cr.fill()
            bowl = cairo.RadialGradient(
                cx - radius * 0.25, cy - radius * 0.28, radius * 0.08, cx, cy, radius
            )
            bowl.add_color_stop_rgba(0.0, shade[0], shade[1], shade[2], 0.1)
            bowl.add_color_stop_rgba(0.55, 0, 0, 0, 0.32 + depth * 0.25)
            bowl.add_color_stop_rgba(1.0, 0, 0, 0, 0.02)
            cr.set_source(bowl)
            cr.arc(cx, cy, radius * 0.92, 0, math.tau)
            cr.fill()
            cr.set_source_rgba(lit[0], lit[1], lit[2], 0.3 + depth * 0.25)
            cr.set_line_width(max(1.2, radius * 0.1))
            cr.arc(cx, cy, radius * 0.86, math.pi * 0.9, math.pi * 1.55)
            cr.stroke()


def _asteroid_path(cr: cairo.Context, width: float, height: float) -> None:
    """Closed irregular blob with protrusions and bites — not a rounded rectangle."""
    cx, cy = width * 0.5, height * 0.5
    rx, ry = width * 0.47, height * 0.45
    count = 32
    rng = random.Random(0x51B0B1E ^ (int(width) * 131) ^ (int(height) * 17))

    radii: list[float] = []
    for i in range(count):
        base = 0.82 + 0.14 * math.sin(i * 0.85) * math.cos(i * 0.31)
        bump = rng.uniform(-0.14, 0.18)
        roll = rng.random()
        if roll < 0.16:
            bump += rng.uniform(0.14, 0.30)  # protrusion
        elif roll < 0.28:
            bump -= rng.uniform(0.12, 0.26)  # concavity
        radii.append(max(0.58, min(1.15, base + bump)))

    smoothed = [
        radii[(i - 1) % count] * 0.18 + radii[i] * 0.64 + radii[(i + 1) % count] * 0.18
        for i in range(count)
    ]

    points: list[tuple[float, float]] = []
    for i, radius in enumerate(smoothed):
        angle = (i / count) * math.tau - math.pi * 0.5
        warp = 1.0 + 0.07 * math.sin(angle * 2.0 + 0.35)
        squash = 1.0 + 0.05 * math.cos(angle * 3.0 - 0.2)
        points.append(
            (
                cx + math.cos(angle) * rx * radius * warp,
                cy + math.sin(angle) * ry * radius * squash,
            )
        )

    cr.new_path()
    # Catmull-Rom-ish curves through midpoints for a smooth organic rim.
    for i in range(count):
        p0 = points[(i - 1) % count]
        p1 = points[i]
        p2 = points[(i + 1) % count]
        p3 = points[(i + 2) % count]
        if i == 0:
            cr.move_to(p1[0], p1[1])
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        cr.curve_to(c1x, c1y, c2x, c2y, p2[0], p2[1])
    cr.close_path()


def _palette(theme):
    if theme is None:
        return (0.30, 0.28, 0.26), (0.38, 0.36, 0.34), (0.62, 0.58, 0.52), (0.12, 0.11, 0.12)
    bg = color_to_rgb(theme.colors.surface)
    border = color_to_rgb(theme.colors.border)
    rock = (
        0.22 + bg[0] * 0.25 + border[0] * 0.12,
        0.20 + bg[1] * 0.22 + border[1] * 0.10,
        0.18 + bg[2] * 0.20 + border[2] * 0.08,
    )
    mid = (rock[0] * 1.15, rock[1] * 1.12, rock[2] * 1.1)
    lit = (
        min(0.78, rock[0] * 1.7 + 0.12),
        min(0.74, rock[1] * 1.65 + 0.1),
        min(0.68, rock[2] * 1.55 + 0.08),
    )
    shade = (rock[0] * 0.35, rock[1] * 0.32, rock[2] * 0.34)
    return rock, mid, lit, shade
