"""Full-bleed spectrum background painter for the active-window block."""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gtk

_BAR_GAP = 2
_MIN_BAR_WIDTH = 2
_PEAK_HEIGHT = 2.0


def paint_spectrum(
    cr,
    *,
    width: int,
    height: int,
    bars: tuple[float, ...],
    colors: tuple[tuple[float, float, float, float], ...],
    peaks: tuple[float, ...] = (),
    gap: int | None = None,
    min_bar_width: int | None = None,
    min_bar_height: int | None = None,
    peak_height: float | None = None,
    fill_width: bool = False,
    corner_radius: float | None = None,
) -> None:
    """Draw frequency-colored bars from the bottom of the allocation."""
    if width <= 0 or height <= 0 or not bars:
        return

    bar_count = len(bars)
    gap = _BAR_GAP if gap is None else max(0, int(gap))
    min_width = _MIN_BAR_WIDTH if min_bar_width is None else max(1, int(min_bar_width))
    floor = 3 if min_bar_height is None else max(1, int(min_bar_height))
    peak_px = _PEAK_HEIGHT if peak_height is None else max(1.0, float(peak_height))

    if fill_width:
        usable = max(bar_count, width - gap * (bar_count - 1))
        base = max(1, usable // bar_count)
        leftover = max(0, usable - base * bar_count)
        widths = [base + (1 if index < leftover else 0) for index in range(bar_count)]
        x = 0.0
    else:
        bar_width = max(min_width, (width - gap * (bar_count - 1)) // bar_count)
        total = bar_width * bar_count + gap * (bar_count - 1)
        x = float(max(0, (width - total) // 2))
        widths = [bar_width] * bar_count

    for index, level in enumerate(bars):
        bar_width = widths[index]
        if corner_radius is None:
            radius = min(2.5, bar_width / 2.0)
        else:
            radius = max(0.0, float(corner_radius))
        color = colors[index] if index < len(colors) else None
        if level > 0.001 and color is not None:
            # Slight vertical gradient: denser near the base.
            bar_height = max(floor, int(height * (0.08 + 0.92 * level)))
            y = height - bar_height
            red, green, blue, alpha = color
            # Soften mid-height so titles stay readable.
            center_boost = 1.0 - 0.18 * math.sin(
                math.pi * ((x + bar_width * 0.5) / max(1, width))
            )
            cr.set_source_rgba(red, green, blue, alpha * center_boost)
            _rounded_rect(cr, x, y, bar_width, bar_height, radius)
            cr.fill()

        if color is not None and index < len(peaks) and peaks[index] > 0.04:
            peak_level = peaks[index]
            peak_y = height - max(peak_px, height * (0.08 + 0.92 * peak_level))
            red, green, blue, alpha = color
            cr.set_source_rgba(red, green, blue, min(0.55, alpha + 0.18))
            cr.rectangle(x, peak_y, bar_width, peak_px)
            cr.fill()

        x += bar_width + gap


def _rounded_rect(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = min(radius, width / 2.0, height / 2.0)
    if radius <= 0.5:
        cr.rectangle(x, y, width, height)
        return
    cr.new_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2.0, 0.0)
    cr.arc(x + width - radius, y + height - radius, radius, 0.0, math.pi / 2.0)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2.0, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    cr.close_path()


class AudioSpectrumWidget(Gtk.DrawingArea):
    """Optional DrawingArea wrapper; ActiveWindow prefers EventBox background paint."""

    def __init__(self, bar_count: int) -> None:
        super().__init__()
        self._bar_count = max(2, bar_count)
        self._bars = tuple(0.0 for _ in range(self._bar_count))
        self._peaks = tuple(0.0 for _ in range(self._bar_count))
        self._colors = tuple((0.0, 0.0, 0.0, 0.0) for _ in range(self._bar_count))
        self.set_sensitive(False)
        self.set_can_focus(False)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.get_style_context().add_class("active-window-spectrum")
        self.connect("draw", self._on_draw)

    def set_frame(
        self,
        bars: tuple[float, ...],
        colors: tuple[tuple[float, float, float, float], ...],
        peaks: tuple[float, ...] = (),
    ) -> None:
        normalized = tuple(
            max(0.0, min(1.0, float(bars[index] if index < len(bars) else 0.0)))
            for index in range(self._bar_count)
        )
        next_peaks = tuple(
            max(0.0, min(1.0, float(peaks[index] if index < len(peaks) else 0.0)))
            for index in range(self._bar_count)
        )
        next_colors = tuple(
            colors[index] if index < len(colors) else (0.0, 0.0, 0.0, 0.0)
            for index in range(self._bar_count)
        )
        if (
            normalized == self._bars
            and next_peaks == self._peaks
            and next_colors == self._colors
        ):
            return
        self._bars = normalized
        self._peaks = next_peaks
        self._colors = next_colors
        self.queue_draw()

    def _on_draw(self, widget: Gtk.DrawingArea, cr) -> bool:
        allocation = widget.get_allocation()
        paint_spectrum(
            cr,
            width=max(1, allocation.width),
            height=max(1, allocation.height),
            bars=self._bars,
            colors=self._colors,
            peaks=self._peaks,
        )
        return False
