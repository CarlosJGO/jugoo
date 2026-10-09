"""Slide a panel in from the right screen edge and back out.

Unlike the puertas (centered pop) and the bar popups (grow out of a bar
block), an edge panel enters from outside the screen. Only the drawing is
translated each frame; the panel keeps its allocation, so nothing re-lays out.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .door import symmetric_ease
from .theme import active_theme

# Same bounds as the task-1 door: long enough to read as movement across a
# full-height panel, short enough not to feel sluggish.
SLIDE_MIN_DURATION_MS = 300
SLIDE_MAX_DURATION_MS = 420


def slide_duration_ms(theme: object | None = None) -> int:
    """Full-travel duration (twice the theme base), clamped; 0 when animations are off."""
    if theme is None:
        theme = active_theme()
    if theme is None:
        return SLIDE_MIN_DURATION_MS
    animation = getattr(theme, "animation", None)
    if animation is None or not animation.enabled:
        return 0
    base = int(animation.duration)
    if base <= 0:
        return 0
    return max(SLIDE_MIN_DURATION_MS, min(SLIDE_MAX_DURATION_MS, base * 2))


def travel_duration_ms(full_ms: int, start: float, target: float) -> int:
    """A reversed slide covers only the remaining distance at the same speed."""
    return int(round(full_ms * min(1.0, abs(target - start))))


def slide_offset(progress: float, width: float) -> float:
    """Horizontal shift in px: ``width`` (fully off-screen) at 0, 0 at 1."""
    progress = max(0.0, min(1.0, progress))
    return (1.0 - progress) * max(0.0, width)


class EdgeSlide(Gtk.Bin):
    """Bin whose child is drawn shifted right by ``(1 - progress) * width``."""

    def __init__(self) -> None:
        super().__init__()
        self.set_has_window(False)
        self._progress = 0.0
        self._tick_id = 0
        self._from = 0.0
        self._to = 0.0
        self._started_us = 0
        self._duration_ms = 0
        self._on_complete: Callable[[bool], None] | None = None
        self._on_frame: Callable[[float], None] | None = None

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def animating(self) -> bool:
        return self._tick_id != 0

    def set_frame_callback(self, callback: Callable[[float], None] | None) -> None:
        """Called with the current progress on every frame (e.g. to fade a backdrop)."""
        self._on_frame = callback

    def apply(self, progress: float) -> None:
        self._progress = max(0.0, min(1.0, progress))
        if self._on_frame is not None:
            self._on_frame(self._progress)
        self.queue_draw()

    def cancel(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._on_complete = None

    def slide_in(self, *, on_complete: Callable[[bool], None] | None = None) -> None:
        self._run(1.0, on_complete)

    def slide_out(self, *, on_complete: Callable[[bool], None] | None = None) -> None:
        self._run(0.0, on_complete)

    def do_draw(self, cr: object) -> bool:
        allocation = self.get_allocation()
        cr.save()
        cr.translate(slide_offset(self._progress, allocation.width), 0.0)
        drawn = Gtk.Bin.do_draw(self, cr)
        cr.restore()
        return drawn

    def _run(self, target: float, on_complete: Callable[[bool], None] | None) -> None:
        self.cancel()
        start = self._progress
        duration = travel_duration_ms(slide_duration_ms(), start, target)
        self._on_complete = on_complete
        if duration <= 0 or start == target:
            self.apply(target)
            self._finish(target)
            return
        self._from = start
        self._to = target
        self._duration_ms = duration
        self._started_us = GLib.get_monotonic_time()
        self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        elapsed_ms = (GLib.get_monotonic_time() - self._started_us) / 1000.0
        t = min(1.0, elapsed_ms / self._duration_ms)
        self.apply(self._from + (self._to - self._from) * symmetric_ease(t))
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        self._finish(self._to)
        return GLib.SOURCE_REMOVE

    def _finish(self, target: float) -> None:
        callback = self._on_complete
        self._on_complete = None
        if callback is not None:
            callback(target >= 1.0)
