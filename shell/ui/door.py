"""Door open/close animation for centered picker overlays (puertas).

Vertical: the card expands from a horizontal midline.
Horizontal: the card expands from a vertical midline.

Uses a scrolled viewport so frames only change clip + scroll — the card is not
re-laid-out every tick (that was the ~15 fps stutter).
"""

from __future__ import annotations

from typing import Callable, Literal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .theme import active_theme

DoorAxis = Literal["vertical", "horizontal"]

# Door needs a bit more time than opacity fades to read as motion, not a pop.
_DOOR_MIN_DURATION_MS = 220
_DOOR_MAX_DURATION_MS = 360
_MIN_MEASURED_PX = 48


def _ease(t: float) -> float:
    """Ease-out cubic — snappy start, soft settle."""
    t = max(0.0, min(1.0, t))
    u = 1.0 - t
    return 1.0 - u * u * u


def _animation_duration_ms() -> int:
    theme = active_theme()
    if theme is None:
        return 260
    if not theme.animation.enabled:
        return 0
    base = int(theme.animation.duration)
    if base <= 0:
        return 0
    # Theme fade duration is tuned for opacity; door motion wants a touch longer.
    return max(_DOOR_MIN_DURATION_MS, min(_DOOR_MAX_DURATION_MS, int(base * 1.6)))


class DoorClip(Gtk.ScrolledWindow):
    """Clips a child to a centered strip that grows like a door."""

    def __init__(self, axis: DoorAxis = "vertical") -> None:
        super().__init__()
        self.get_style_context().add_class("picker-door-clip")
        # EXTERNAL: scroll via adjustments, never show bars, never squash the child.
        self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.set_overlay_scrolling(True)
        self.set_hexpand(False)
        self.set_vexpand(False)

        self._axis: DoorAxis = axis
        self._progress = 1.0
        self._full_w = 1
        self._full_h = 1
        self._view_w = 1
        self._view_h = 1
        self._child: Gtk.Widget | None = None
        self._tick_id = 0
        self._anim_from = 1.0
        self._anim_to = 1.0
        self._anim_started_us = 0
        self._anim_duration_ms = 0
        self._on_complete: Callable[[bool], None] | None = None
        self._opening = True
        self.connect("size-allocate", self._on_size_allocate)

    @property
    def axis(self) -> DoorAxis:
        return self._axis

    @axis.setter
    def axis(self, value: DoorAxis) -> None:
        self._axis = value

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
        """Apply a door frame. ``progress`` 0 is shut, 1 is fully open."""
        if self._child is None:
            return
        self._progress = max(0.0, min(1.0, progress))
        fw, fh = self._full_w, self._full_h
        if self._axis == "vertical":
            width = fw
            height = max(1, int(round(fh * self._progress)))
        else:
            width = max(1, int(round(fw * self._progress)))
            height = fh
        if width == self._view_w and height == self._view_h:
            self._center_scroll()
            return
        self._view_w = width
        self._view_h = height
        self.set_size_request(width, height)
        self._center_scroll()

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
        eased = _ease(t)
        progress = self._anim_from + (self._anim_to - self._anim_from) * eased
        self.apply(progress)
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        callback = self._on_complete
        self._on_complete = None
        if callback is not None:
            callback(self._opening)
        return GLib.SOURCE_REMOVE
