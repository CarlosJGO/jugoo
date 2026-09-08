"""Meteor silhouette: irregular body + trailing plume as one alpha mask."""

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


class MeteorDisguise:
    id = DisguiseId.METEOR

    def wrap(self, content: Gtk.Widget) -> Gtk.Widget:
        return ShapedDisguiseHost(self, content, animate=True)

    def style_widget(self, widget: Gtk.Widget) -> None:
        add_disguise_classes(widget, self.id, *self.style_classes())

    def style_classes(self) -> tuple[str, ...]:
        return ("disguise-meteor-surface",)

    def build_shape(self, cr: cairo.Context, width: float, height: float) -> None:
        _meteor_path(cr, width, height)

    def content_insets(self, width: float, height: float) -> ContentInsets:
        # Leave the left trail outside the GTK content pocket.
        return ContentInsets(
            top=max(14, int(height * 0.10)),
            right=max(18, int(width * 0.06)),
            bottom=max(14, int(height * 0.10)),
            left=max(52, int(width * 0.22)),
        )

    def render(
        self,
        cr: cairo.Context,
        width: float,
        height: float,
        *,
        elapsed_ms: int = 0,
    ) -> None:
        ember, core, trail = _palette(active_theme())
        t = elapsed_ms / 1000.0
        body_x = width * 0.22

        # Trail heat inside the clipped silhouette
        plume = cairo.LinearGradient(0, height * 0.5, body_x + 8, height * 0.5)
        plume.add_color_stop_rgba(0.0, trail[0], trail[1], trail[2], 0.05)
        plume.add_color_stop_rgba(0.55, ember[0], ember[1], ember[2], 0.35)
        plume.add_color_stop_rgba(1.0, core[0], core[1], core[2], 0.55)
        cr.set_source(plume)
        cr.paint()

        # Body core
        body = cairo.LinearGradient(body_x, 0, width, height)
        body.add_color_stop_rgba(0.0, ember[0], ember[1], ember[2], 0.55)
        body.add_color_stop_rgba(0.35, core[0], core[1], core[2], 0.92)
        body.add_color_stop_rgba(1.0, core[0] * 0.7, core[1] * 0.7, core[2] * 0.75, 0.95)
        cr.save()
        cr.rectangle(body_x - 4, 0, width - body_x + 4, height)
        cr.clip()
        cr.set_source(body)
        cr.paint()
        cr.restore()

        # Sparks along the trail
        rng = random.Random(0x7E7010D ^ int(width) ^ (int(height) << 3))
        for i in range(max(10, int(height / 4))):
            sx = rng.uniform(2, body_x)
            sy = rng.uniform(height * 0.15, height * 0.85)
            flicker = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * rng.uniform(2.5, 5.0) + i))
            drift = (t * 22 + i * 1.7) % (body_x + 10)
            x = max(1.0, body_x - drift)
            cr.set_source_rgba(ember[0], ember[1], ember[2], 0.4 * flicker)
            cr.arc(x, sy, rng.uniform(0.7, 1.8), 0, math.tau)
            cr.fill()

        # Hot leading edge
        glow = 0.45 + 0.25 * math.sin(t * 5.0)
        cr.set_source_rgba(ember[0], ember[1], ember[2], glow)
        cr.set_line_width(2.5)
        cr.move_to(body_x + 8, height * 0.12)
        cr.line_to(width * 0.92, height * 0.18)
        cr.stroke()


def _meteor_path(cr: cairo.Context, width: float, height: float) -> None:
    """Asymmetric body on the right + tapered trail extending left."""
    rng = random.Random(0x7E7E02 ^ int(width * 3) ^ int(height * 5))

    body_left = width * 0.20
    cx = width * 0.62
    cy = height * 0.50
    rx = width * 0.34
    ry = height * 0.40
    count = 22
    radii = []
    for i in range(count):
        base = 0.85 + 0.1 * math.sin(i * 0.7)
        bump = rng.uniform(-0.12, 0.16)
        if rng.random() < 0.2:
            bump += rng.uniform(0.08, 0.2)
        radii.append(max(0.62, min(1.1, base + bump)))
    smoothed = [
        radii[(i - 1) % count] * 0.2 + radii[i] * 0.6 + radii[(i + 1) % count] * 0.2
        for i in range(count)
    ]

    body: list[tuple[float, float]] = []
    for i, radius in enumerate(smoothed):
        angle = (i / count) * math.tau
        body.append((cx + math.cos(angle) * rx * radius, cy + math.sin(angle) * ry * radius))

    tip_y = cy + rng.uniform(-height * 0.04, height * 0.04)
    top = cy - height * 0.22
    bot = cy + height * 0.22

    cr.new_path()
    cr.move_to(width * 0.02, tip_y)
    cr.curve_to(
        width * 0.08,
        tip_y - height * 0.08,
        body_left,
        top,
        body[0][0],
        body[0][1],
    )
    for i in range(count):
        p0 = body[(i - 1) % count]
        p1 = body[i]
        p2 = body[(i + 1) % count]
        p3 = body[(i + 2) % count]
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        if i == 0:
            cr.line_to(p1[0], p1[1])
        cr.curve_to(c1x, c1y, c2x, c2y, p2[0], p2[1])
    cr.curve_to(
        body_left,
        bot,
        width * 0.08,
        tip_y + height * 0.08,
        width * 0.02,
        tip_y,
    )
    cr.close_path()


def _palette(theme):
    if theme is None:
        return (1.0, 0.55, 0.25), (0.12, 0.1, 0.16), (0.85, 0.7, 0.45)
    accent = color_to_rgb(theme.colors.accent)
    warning = color_to_rgb(theme.colors.warning)
    surface = color_to_rgb(theme.colors.surface_alt)
    ember = (
        min(1.0, warning[0] * 0.65 + accent[0] * 0.35),
        min(1.0, warning[1] * 0.55 + accent[1] * 0.25),
        min(1.0, warning[2] * 0.35 + accent[2] * 0.2),
    )
    core = (surface[0] * 0.9, surface[1] * 0.85, surface[2] * 0.9)
    trail = (
        min(1.0, ember[0] * 0.9 + 0.1),
        min(1.0, ember[1] * 0.85 + 0.08),
        min(1.0, ember[2] * 0.7 + 0.05),
    )
    return ember, core, trail
