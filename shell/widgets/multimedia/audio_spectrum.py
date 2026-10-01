"""Full-bleed spectrum background painter for the active-window block."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gtk

from ... import config as shell_config
from ...ui.theme import active_theme, color_to_rgb

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


def paint_waveform(
    cr,
    *,
    width: int,
    height: int,
    bars: tuple[float, ...],
    colors: tuple[tuple[float, float, float, float], ...],
    line_width: float | None = None,
) -> None:
    """Build a breathing nebula-like contour directly from the audio amplitudes."""
    if width <= 0 or height <= 0 or not bars:
        return

    def _body_level(level: float) -> float:
        value = max(0.0, min(1.0, float(level)))
        if value <= 0.0:
            return 0.0
        return max(0.0, min(1.0, (value / (0.24 + value)) ** 1.35))

    theme = active_theme()
    center_x = width / 2.0
    center_y = height / 2.0
    stroke_width = 2.0 if line_width is None else max(1.0, float(line_width))
    sample_count = len(bars)
    half_span = width * 0.44

    top_points: list[tuple[float, float]] = []
    bottom_points: list[tuple[float, float]] = []
    for index, level in enumerate(bars):
        normalized = max(0.0, min(1.0, float(level)))
        left = bars[max(0, index - 1)] if index > 0 else normalized
        right = bars[min(len(bars) - 1, index + 1)] if index < len(bars) - 1 else normalized
        local = (normalized * 0.52) + (left * 0.24) + (right * 0.24)
        body_level = _body_level(local)
        edge = abs((index / max(1, sample_count - 1)) - 0.5) * 2.0
        core = max(0.0, 1.0 - edge)
        x = center_x + (((index / max(1, sample_count - 1)) - 0.5) * width * 0.96)

        # Keep the contour collapsed at rest, but let genuine energy drive a much wider
        # expansion as the sound gets louder or more transient.
        radius = 8.0 + (body_level * 52.0) + (core * 16.0)
        micro = math.sin(index * 1.9 + local * 11.0) * (2.0 + body_level * 16.0)
        micro2 = math.cos(index * 2.7 + local * 8.5) * (1.5 + body_level * 8.0)
        top_amp = radius * (0.18 + (0.42 * body_level) + (0.12 * core))
        bottom_amp = radius * (0.20 + (0.54 * body_level) + (0.16 * core))

        top_y = center_y - top_amp + micro
        bottom_y = center_y + bottom_amp + micro2

        # Push the strongest motion toward the center while letting the side wings breathe.
        if core > 0.1:
            top_y += (1.0 - core) * 10.0
            bottom_y -= (1.0 - core) * 8.0

        top_points.append((x, top_y))
        bottom_points.append((x, bottom_y))

    if not top_points:
        return

    cr.new_path()
    cr.move_to(top_points[0][0], top_points[0][1])
    for point in top_points[1:]:
        cr.line_to(point[0], point[1])
    for point in reversed(bottom_points[1:]):
        cr.line_to(point[0], point[1])
    cr.line_to(bottom_points[0][0], bottom_points[0][1])
    cr.close_path()

    if theme is not None:
        accent = color_to_rgb(theme.colors.accent)
        primary = color_to_rgb(theme.colors.primary)
        secondary = color_to_rgb(theme.colors.secondary)
        gradient = cairo.LinearGradient(
            center_x,
            center_y - height * 0.35,
            center_x,
            center_y + height * 0.35,
        )
        gradient.add_color_stop_rgba(0.0, accent[0], accent[1], accent[2], 0.78)
        gradient.add_color_stop_rgba(0.42, primary[0], primary[1], primary[2], 0.54)
        gradient.add_color_stop_rgba(1.0, secondary[0], secondary[1], secondary[2], 0.16)
        cr.set_source(gradient)
    else:
        base_color = colors[0] if colors else (0.35, 0.82, 1.0, 1.0)
        cr.set_source_rgba(base_color[0], base_color[1], base_color[2], 0.6)

    cr.fill_preserve()

    glow_radius = max(24.0, min(width, height) * 0.42)
    glow = cairo.RadialGradient(center_x, center_y, 0.0, center_x, center_y, glow_radius)
    if theme is not None:
        accent = color_to_rgb(theme.colors.accent)
        primary = color_to_rgb(theme.colors.primary)
        glow.add_color_stop_rgba(0.0, accent[0], accent[1], accent[2], 0.60)
        glow.add_color_stop_rgba(0.42, primary[0], primary[1], primary[2], 0.18)
        glow.add_color_stop_rgba(1.0, primary[0], primary[1], primary[2], 0.0)
    else:
        glow.add_color_stop_rgba(0.0, 0.55, 0.88, 1.0, 0.34)
        glow.add_color_stop_rgba(1.0, 0.20, 0.50, 0.95, 0.0)
    cr.set_source(glow)
    cr.arc(center_x, center_y, glow_radius, 0, 2 * math.pi)
    cr.fill()

    cr.set_source_rgba(0.95, 0.99, 1.0, 0.18)
    cr.set_line_width(max(1.0, stroke_width * 0.7))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.stroke()

    cr.set_source_rgba(0.96, 0.98, 1.0, 0.08)
    cr.set_line_width(1.0)
    cr.move_to(center_x - half_span, center_y)
    cr.line_to(center_x + half_span, center_y)
    cr.stroke()


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
        if str(getattr(shell_config, "AUDIO_VISUALIZER_STYLE", "cava")).lower() == "waveform":
            paint_waveform(
                cr,
                width=max(1, allocation.width),
                height=max(1, allocation.height),
                bars=self._bars,
                colors=self._colors,
            )
            return False
        paint_spectrum(
            cr,
            width=max(1, allocation.width),
            height=max(1, allocation.height),
            bars=self._bars,
            colors=self._colors,
            peaks=self._peaks,
        )
        return False
