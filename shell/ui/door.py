"""Door open/close animation for centered picker overlays (puertas).

Vertical: the card expands from a horizontal midline.
Horizontal: the card expands from a vertical midline.

Uses a scrolled viewport so frames only change clip + scroll — the card is not
re-laid-out every tick (that was the ~15 fps stutter).
"""

from __future__ import annotations

from math import cos, pi
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .theme import active_theme

# Keep the card allocation stable; only its drawing and opacity change per frame.
_POP_MIN_DURATION_MS = 140
_POP_MAX_DURATION_MS = 220
_POP_START_SCALE = 0.78
_MIN_MEASURED_PX = 48


def _ease(t: float) -> float:
    """Cosine ease-in-out, used for the close transition."""
    t = max(0.0, min(1.0, t))
    return 0.5 - 0.5 * cos(pi * t)


def _ease_pop(t: float) -> float:
    """Ease out with a small overshoot, like a bubble settling into place."""
    t = max(0.0, min(1.0, t))
    if t >= 1.0:
        return 1.0
    overshoot = 1.70158
    return 1.0 + (overshoot + 1.0) * (t - 1.0) ** 3 + overshoot * (t - 1.0) ** 2


def _animation_duration_ms() -> int:
    theme = active_theme()
    if theme is None:
        return 190
    if not theme.animation.enabled:
        return 0
    base = int(theme.animation.duration)
    if base <= 0:
        return 0
    return max(_POP_MIN_DURATION_MS, min(_POP_MAX_DURATION_MS, base))


class BubblePop(Gtk.Bin):
    """Animate a card with a centered scale and opacity, without relayout."""

    def __init__(self) -> None:
        super().__init__()
        self.set_has_window(False)

        self._progress = 1.0
        self._scale = 1.0
        self._full_w = 1
        self._full_h = 1
        self._child: Gtk.Widget | None = None
        self._tick_id = 0
        self._anim_from = 1.0
        self._anim_to = 1.0
        self._anim_started_us = 0
        self._anim_duration_ms = 0
        self._on_complete: Callable[[bool], None] | None = None
        self._opening = True

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def animating(self) -> bool:
        return self._tick_id != 0

    @property
    def full_size(self) -> tuple[int, int]:
        return self._full_w, self._full_h

    def set_child(self, child: Gtk.Widget) -> None:
        current = self.get_child()
        if current is not None:
            self.remove(current)
        self._child = child
        self.add(child)

    def cancel(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._on_complete = None

    def seed_size(self, width: int, height: int) -> None:
        """Prime target size before the first real measure (avoids 1×1 first open)."""
        self._full_w = max(1, int(width))
        self._full_h = max(1, int(height))
        child = self._child
        if child is not None:
            child.set_size_request(self._full_w, self._full_h)

    def capture_full_size(self) -> tuple[int, int]:
        """Measure the child without expanding the viewport (no open-state flash)."""
        child = self._child
        if child is None:
            self._full_w, self._full_h = 1, 1
            return self._full_w, self._full_h

        # Unlock only the child requisition; keep the current viewport clip intact.
        child.set_size_request(-1, -1)
        _min_req, nat_req = child.get_preferred_size()
        width = max(1, int(nat_req.width))
        height = max(1, int(nat_req.height))
        if width >= _MIN_MEASURED_PX:
            self._full_w = width
        if height >= _MIN_MEASURED_PX:
            self._full_h = height
        child.set_size_request(self._full_w, self._full_h)
        # Re-apply current progress so size_request stays on the clip, not -1.
        self.apply(self._progress)
        return self._full_w, self._full_h

    def size_ready(self) -> bool:
        return self._full_w >= _MIN_MEASURED_PX and self._full_h >= _MIN_MEASURED_PX

    def apply(self, progress: float) -> None:
        """Apply a pop frame. ``progress`` 0 is hidden, 1 is fully open."""
        if self._child is None:
            return
        self._progress = max(0.0, min(1.0, progress))
        self._scale = _POP_START_SCALE + (1.0 - _POP_START_SCALE) * self._progress
        self.set_opacity(self._progress)
        self.queue_draw()

    def do_draw(self, cr: object) -> bool:
        allocation = self.get_allocation()
        cr.save()
        cr.translate(allocation.width * 0.5, allocation.height * 0.5)
        cr.scale(self._scale, self._scale)
        cr.translate(-allocation.width * 0.5, -allocation.height * 0.5)
        drawn = Gtk.Bin.do_draw(self, cr)
        cr.restore()
        return drawn

    def open(
        self,
        *,
        on_complete: Callable[[bool], None] | None = None,
        from_progress: float | None = None,
    ) -> None:
        self._run(opening=True, on_complete=on_complete, from_progress=from_progress)

    def close(
        self,
        *,
        on_complete: Callable[[bool], None] | None = None,
        from_progress: float | None = None,
    ) -> None:
        self._run(opening=False, on_complete=on_complete, from_progress=from_progress)

    def snap_open(self) -> None:
        self.cancel()
        self.capture_full_size()
        self.apply(1.0)

    def snap_shut(self) -> None:
        self.cancel()
        self.apply(0.0)

    def _center_scroll(self) -> None:
        fw, fh = float(self._full_w), float(self._full_h)
        view_w, view_h = float(self._view_w), float(self._view_h)
        hadj = self.get_hadjustment()
        vadj = self.get_vadjustment()
        # Prefer set_value after configure once; avoid reconfigure storms when unchanged.
        if hadj is not None:
            upper = max(fw, view_w)
            page = min(view_w, upper)
            target = max(0.0, (fw - view_w) * 0.5)
            if (
                abs(hadj.get_upper() - upper) > 0.5
                or abs(hadj.get_page_size() - page) > 0.5
            ):
                hadj.configure(target, 0.0, upper, 1.0, page, page)
            elif abs(hadj.get_value() - target) > 0.5:
                hadj.set_value(target)
        if vadj is not None:
            upper = max(fh, view_h)
            page = min(view_h, upper)
            target = max(0.0, (fh - view_h) * 0.5)
            if (
                abs(vadj.get_upper() - upper) > 0.5
                or abs(vadj.get_page_size() - page) > 0.5
            ):
                vadj.configure(target, 0.0, upper, 1.0, page, page)
            elif abs(vadj.get_value() - target) > 0.5:
                vadj.set_value(target)

    def _on_size_allocate(self, *_args) -> None:
        if self._child is not None:
            self._center_scroll()

    def _run(
        self,
        *,
        opening: bool,
        on_complete: Callable[[bool], None] | None,
        from_progress: float | None,
    ) -> None:
        self.cancel()
        duration = _animation_duration_ms()
        start = self._progress if from_progress is None else from_progress
        target = 1.0 if opening else 0.0
        self._opening = opening
        self._on_complete = on_complete
        if duration <= 0 or start == target:
            self.apply(target)
            callback = self._on_complete
            self._on_complete = None
            if callback is not None:
                callback(opening)
            return
        self._anim_from = start
        self._anim_to = target
        self._anim_duration_ms = duration
        self._anim_started_us = GLib.get_monotonic_time()
        self.apply(start)
        # VSync-aligned ticks beat a fixed 16 ms timeout under load.
        self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        elapsed_ms = (GLib.get_monotonic_time() - self._anim_started_us) / 1000.0
        t = min(1.0, elapsed_ms / self._anim_duration_ms)
        eased = _ease_pop(t) if self._opening else _ease(t)
        progress = self._anim_from + (self._anim_to - self._anim_from) * eased
        self._progress = max(0.0, min(1.0, progress))
        scale_progress = max(0.0, min(1.12, progress))
        self._scale = _POP_START_SCALE + (1.0 - _POP_START_SCALE) * scale_progress
        self.set_opacity(self._progress)
        self.queue_draw()
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        callback = self._on_complete
        self._on_complete = None
        if callback is not None:
            callback(self._opening)
        return GLib.SOURCE_REMOVE
