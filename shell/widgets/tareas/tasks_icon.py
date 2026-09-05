"""Tasks bar icon with a one-shot pulse. Layout size never changes."""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .pulse import (
    PULSE_TICK_MS,
    pulse_amplitude,
    pulse_duration_ms,
    pulse_progress_scale,
)
from ...servicios.tareas.presencia import STATUS_ALIVE, STATUS_QUIET
from ...servicios.tareas.vigilancia.eventos import (
    KIND_AI_REMINDER,
    KIND_HEARTBEAT,
    KIND_REMINDER,
)

_STATUS_ALPHA = {
    "unknown": 0.62,
    "alive": 1.0,
    "quiet": 0.80,
    "inactive": 0.58,
}


class TasksPulseIcon(Gtk.DrawingArea):
    """Symbolic list icon. Timer exists only while a pulse is playing."""

    def __init__(self, pixel_size: int) -> None:
        super().__init__()
        self._pixel_size = max(12, pixel_size)
        self._presence = "unknown"
        self._kind = KIND_HEARTBEAT
        self._elapsed_ms = 0
        self._source_id = 0
        self._scale = 1.0
        self._glow = 0.0
        self.set_sensitive(False)
        self.set_can_focus(False)
        self.set_size_request(self._pixel_size, self._pixel_size)
        self.get_style_context().add_class("tasks-icon")
        self.connect("draw", self._on_draw)
        self.connect("destroy", self._on_destroy)

    @property
    def source_id(self) -> int:
        return self._source_id

    @property
    def scale(self) -> float:
        return self._scale

    def set_pixel_size(self, pixel_size: int) -> None:
        size = max(12, pixel_size)
        if size == self._pixel_size:
            return
        self._pixel_size = size
        self.set_size_request(size, size)
        self.queue_draw()

    def set_presence(self, status: str) -> None:
        if status == self._presence:
            return
        self._presence = status
        self.queue_draw()

    def pulse(self, kind: str = KIND_HEARTBEAT) -> None:
        self._kind = kind if kind in {KIND_HEARTBEAT, KIND_REMINDER, KIND_AI_REMINDER} else KIND_HEARTBEAT
        self._elapsed_ms = 0
        self._scale = 1.0
        self._glow = 0.0
        if self._source_id:
            return
        self._source_id = GLib.timeout_add(PULSE_TICK_MS, self._tick)

    def stop(self) -> None:
        if self._source_id:
            GLib.source_remove(self._source_id)
            self._source_id = 0
        self._elapsed_ms = 0
        self._scale = 1.0
        self._glow = 0.0
        self.queue_draw()

    def _tick(self) -> bool:
        duration = pulse_duration_ms(self._kind)
        self._elapsed_ms += PULSE_TICK_MS
        progress = self._elapsed_ms / duration
        if progress >= 1.0:
            self._source_id = 0
            self._scale = 1.0
            self._glow = 0.0
            self.queue_draw()
            return False
        self._scale = pulse_progress_scale(progress, pulse_amplitude(self._kind))
        if self._kind in {KIND_REMINDER, KIND_AI_REMINDER}:
            self._glow = math.sin(math.pi * progress)
        else:
            self._glow = 0.18 * math.sin(math.pi * progress)
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
        alpha = _STATUS_ALPHA.get(self._presence, 0.62)
        if self._presence == STATUS_ALIVE:
            alpha = 1.0
        elif self._presence == STATUS_QUIET:
            alpha = 0.80

        if self._glow > 0.04:
            radius = min(cx, cy) * (0.55 + 0.28 * (1.0 - self._glow))
            glow_alpha = (0.16 if self._kind == KIND_HEARTBEAT else 0.28) * self._glow
            cr.set_source_rgba(fg.red, fg.green, fg.blue, glow_alpha)
            cr.set_line_width(1.1)
            cr.arc(cx, cy, radius, 0.0, 2.0 * math.pi)
            cr.stroke()

        pixbuf = self._symbolic_pixbuf(fg)
        if pixbuf is None:
            return False
        cr.save()
        cr.translate(cx, cy)
        cr.scale(self._scale, self._scale)
        Gdk.cairo_set_source_pixbuf(
            cr,
            pixbuf,
            -pixbuf.get_width() / 2.0,
            -pixbuf.get_height() / 2.0,
        )
        cr.paint_with_alpha(alpha)
        cr.restore()
        return False

    def _symbolic_pixbuf(self, fg):
        theme = Gtk.IconTheme.get_default()
        info = theme.lookup_icon(
            "view-list-symbolic",
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
