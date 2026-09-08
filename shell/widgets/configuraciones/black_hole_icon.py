"""Black-hole symbolic icon for the Settings Center entry."""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gtk


class BlackHoleIcon(Gtk.DrawingArea):
    """Drawn accretion-disk / event-horizon mark — fills its allocated block."""

    def __init__(self, pixel_size: int = 22) -> None:
        super().__init__()
        self._pixel_size = max(14, pixel_size)
        self.set_sensitive(False)
        self.set_can_focus(False)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.set_size_request(self._pixel_size, self._pixel_size)
        self.get_style_context().add_class("settings-black-hole")
        self.connect("draw", self._on_draw)

    def set_pixel_size(self, pixel_size: int) -> None:
        size = max(14, pixel_size)
        if size == self._pixel_size:
            return
        self._pixel_size = size
        self.set_size_request(size, size)
        self.queue_draw()

    def _on_draw(self, _widget: Gtk.Widget, cr) -> bool:
        allocation = self.get_allocation()
        width = float(max(allocation.width, self._pixel_size))
        height = float(max(allocation.height, self._pixel_size))
        size = min(width, height)

        style = self.get_style_context()
        success, color = style.lookup_color("theme_primary")
        if not success:
            success, color = style.lookup_color("shell_accent")
        if success:
            red, green, blue, alpha = color.red, color.green, color.blue, color.alpha
        else:
            red, green, blue, alpha = 0.49, 0.55, 1.0, 1.0

        muted_ok, muted = style.lookup_color("theme_text_muted")
        if muted_ok:
            mr, mg, mb = muted.red, muted.green, muted.blue
        else:
            mr, mg, mb = 0.57, 0.60, 0.71

        cx = width * 0.5
        cy = height * 0.5
        # Fill the block: outer ring nearly touches the edges.
        outer = size * 0.48
        mid = size * 0.34
        hole = size * 0.22

        # Soft outer glow
        cr.set_source_rgba(red, green, blue, alpha * 0.22)
        cr.arc(cx, cy, outer, 0, 2 * math.pi)
        cr.fill()

        # Accretion disk (elliptical, edge-on feel) — large, fills the block
        cr.save()
        cr.translate(cx, cy)
        cr.scale(1.0, 0.62)
        cr.set_source_rgba(red, green, blue, alpha * 0.55)
        cr.set_line_width(max(1.4, size * 0.10))
        cr.arc(0, 0, outer * 0.92, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(red, green, blue, alpha * 0.9)
        cr.set_line_width(max(1.2, size * 0.07))
        cr.arc(0, 0, mid * 1.05, 0.05 * math.pi, 1.95 * math.pi)
        cr.stroke()
        cr.restore()

        # Photon ring
        cr.set_source_rgba(mr, mg, mb, 0.95)
        cr.set_line_width(max(1.2, size * 0.055))
        cr.arc(cx, cy, hole * 1.35, 0, 2 * math.pi)
        cr.stroke()

        # Event horizon — dominant center
        cr.set_source_rgba(0.02, 0.03, 0.06, 0.98)
        cr.arc(cx, cy, hole, 0, 2 * math.pi)
        cr.fill()

        # Specular rim on the horizon
        cr.set_source_rgba(red, green, blue, alpha * 0.7)
        cr.set_line_width(max(1.0, size * 0.045))
        cr.arc(cx, cy, hole * 0.94, -0.55 * math.pi, 0.4 * math.pi)
        cr.stroke()
        return False
