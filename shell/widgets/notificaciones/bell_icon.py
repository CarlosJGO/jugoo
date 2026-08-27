"""Decorative notification bell with a damped ring animation."""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import NOTIFICATIONS_BELL_ANIMATION_MS

_TICK_MS = 16
_MAX_IMPULSE = 1.35


class BellIcon(Gtk.DrawingArea):
    """Symbolic bell that rings in-place without changing widget layout."""

    def __init__(self, pixel_size: int) -> None:
        super().__init__()
        self._pixel_size = max(12, pixel_size)
        self._impulse = 0.0
        self._elapsed_ms = 0
        self._angle = 0.0
        self._wobble = 0.0
        self._ring = 0.0
        self._source_id = 0
        self.set_sensitive(False)
        self.set_can_focus(False)
        self.set_size_request(self._pixel_size, self._pixel_size)
        self.get_style_context().add_class("notifications-bell")
        self.connect("draw", self._on_draw)
        self.connect("destroy", self._on_destroy)

    def set_pixel_size(self, pixel_size: int) -> None:
        size = max(12, pixel_size)
        if size == self._pixel_size:
            return
        self._pixel_size = size
        self.set_size_request(size, size)
        self.queue_draw()

    def ring(self) -> None:
        leftover = abs(self._impulse) * 0.45 if self._source_id else 0.0
        self._impulse = min(_MAX_IMPULSE, leftover + 1.0)
        self._elapsed_ms = 0
        if self._source_id:
            return
        self._source_id = GLib.timeout_add(_TICK_MS, self._tick)

    def stop(self) -> None:
        if self._source_id:
            GLib.source_remove(self._source_id)
            self._source_id = 0
        self._impulse = 0.0
        self._elapsed_ms = 0
        self._angle = 0.0
        self._wobble = 0.0
        self._ring = 0.0
        self.queue_draw()

    def _tick(self) -> bool:
        self._elapsed_ms += _TICK_MS
        t = self._elapsed_ms / 1000.0
        duration = NOTIFICATIONS_BELL_ANIMATION_MS / 1000.0
        envelope = math.exp(-t / 0.16) * self._impulse
        if envelope < 0.02 and t >= duration * 0.7:
            self._source_id = 0
            self._impulse = 0.0
            self._angle = 0.0
            self._wobble = 0.0
            self._ring = 0.0
            self.queue_draw()
            return False
        self._angle = envelope * 0.34 * math.sin(2.0 * math.pi * 7.5 * t)
        self._wobble = envelope * 0.11 * math.sin(2.0 * math.pi * 13.0 * t)
        self._ring = max(0.0, (1.0 - t / 0.42) * min(1.0, self._impulse))
        self.queue_draw()
        return True

    def _on_draw(self, widget: Gtk.DrawingArea, cr) -> bool:
        allocation = widget.get_allocation()
        width = max(1, allocation.width)
        height = max(1, allocation.height)
        cx = width / 2.0
        cy = height / 2.0
        style = widget.get_style_context()
        fg = style.get_color(Gtk.StateFlags.NORMAL)

        if self._ring > 0.04:
            radius = min(cx, cy) * (0.42 + 0.52 * (1.0 - self._ring))
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.22 * self._ring)
            cr.set_line_width(1.15)
            cr.arc(cx, cy, radius, 0.0, 2.0 * math.pi)
            cr.stroke()

        pixbuf = self._symbolic_pixbuf(fg)
        if pixbuf is None:
            return False

        cr.save()
        cr.translate(cx, cy - 0.6)
        cr.rotate(self._angle + self._wobble)
        Gdk.cairo_set_source_pixbuf(
            cr,
            pixbuf,
            -pixbuf.get_width() / 2.0,
            -pixbuf.get_height() / 2.0 + 0.6,
        )
        cr.paint()
        cr.restore()
        return False

    def _symbolic_pixbuf(self, fg):
        theme = Gtk.IconTheme.get_default()
        info = theme.lookup_icon(
            "notifications-symbolic",
            self._pixel_size,
            Gtk.IconLookupFlags.FORCE_SYMBOLIC,
        )
        if info is None:
            return None
        try:
            pixbuf, _was_symbolic = info.load_symbolic(fg, None, None, None)
        except Exception:
            return None
        return pixbuf

    def _on_destroy(self, *_args) -> None:
        self.stop()
