"""Tiny cava painted behind workspace icons while audio is active."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import GLib

from ...config import WORKSPACE_MINI_CAVA_BARS, WORKSPACE_MINI_CAVA_FPS
from ...servicios.audio.audio_levels import frequency_to_rgba, logarithmic_band_centers
from ...ui.theme import active_theme, color_to_rgb
from ..multimedia.audio_spectrum import paint_spectrum

_MUTED_PALETTE = ((0.86, 0.28, 0.32), (0.78, 0.22, 0.28), (0.72, 0.18, 0.24))


class WorkspaceMiniCava:
    """Animated spectrum state; paints into a parent cairo context (behind icons)."""

    def __init__(
        self,
        *,
        bar_count: int | None = None,
        on_redraw: Callable[[], None] | None = None,
    ) -> None:
        self._bar_count = max(3, int(bar_count or WORKSPACE_MINI_CAVA_BARS))
        self._bars = tuple(0.0 for _ in range(self._bar_count))
        self._peaks = tuple(0.0 for _ in range(self._bar_count))
        self._colors = tuple((0.0, 0.0, 0.0, 0.0) for _ in range(self._bar_count))
        self._has_audio = False
        self._playing = False
        self._muted = False
        self._live_bars: tuple[float, ...] | None = None
        self._tick_id = 0
        self._phase = 0.0
        self._on_redraw = on_redraw

    def set_redraw(self, on_redraw: Callable[[], None] | None) -> None:
        self._on_redraw = on_redraw

    def set_audio_state(self, *, has_audio: bool, playing: bool, muted: bool) -> None:
        changed = (
            has_audio != self._has_audio
            or playing != self._playing
            or muted != self._muted
        )
        self._has_audio = has_audio
        self._playing = playing
        self._muted = muted
        if not has_audio:
            self._live_bars = None
            self._stop_tick()
            self._bars = tuple(0.0 for _ in range(self._bar_count))
            self._peaks = self._bars
            self._colors = tuple((0.0, 0.0, 0.0, 0.0) for _ in range(self._bar_count))
            self._request_redraw()
            return
        if playing or muted:
            self._ensure_tick()
        else:
            self._stop_tick()
            self._apply_idle_frame()
        if changed:
            self._request_redraw()

    def set_live_bars(self, bars: tuple[float, ...] | None) -> None:
        """Optional real spectrum from the bar cava (downsampled to mini count)."""
        if bars is None:
            if self._live_bars is None:
                return
            self._live_bars = None
            return
        if not bars:
            self._live_bars = None
            return
        step = len(bars) / float(self._bar_count)
        sampled: list[float] = []
        for index in range(self._bar_count):
            start = int(index * step)
            end = max(start + 1, int((index + 1) * step))
            chunk = bars[start:end]
            sampled.append(sum(chunk) / len(chunk) if chunk else 0.0)
        self._live_bars = tuple(max(0.0, min(1.0, value)) for value in sampled)

    def paint(self, cr, width: int, height: int) -> None:
        if not self._has_audio or width <= 0 or height <= 0:
            return
        paint_spectrum(
            cr,
            width=width,
            height=height,
            bars=self._bars,
            colors=self._colors,
            peaks=self._peaks,
            gap=1,
            min_bar_width=1,
            min_bar_height=1,
            peak_height=1.0,
            fill_width=True,
            corner_radius=0.0,
        )

    def close(self) -> None:
        self._stop_tick()

    def _request_redraw(self) -> None:
        if self._on_redraw is not None:
            self._on_redraw()

    def _ensure_tick(self) -> None:
        if self._tick_id:
            return
        interval = max(16, int(1000 / max(1, WORKSPACE_MINI_CAVA_FPS)))
        self._tick_id = GLib.timeout_add(interval, self._on_tick)

    def _stop_tick(self) -> None:
        if not self._tick_id:
            return
        GLib.source_remove(self._tick_id)
        self._tick_id = 0

    def _on_tick(self) -> bool:
        if not self._has_audio or (not self._playing and not self._muted):
            self._tick_id = 0
            return False
        self._phase = time.monotonic()
        if self._live_bars is not None and self._playing and not self._muted:
            self._apply_live_frame(self._live_bars)
        else:
            self._apply_synthetic_frame()
        self._request_redraw()
        return True

    def _palette(self) -> tuple[tuple[float, float, float], ...]:
        if self._muted:
            return _MUTED_PALETTE
        theme = active_theme()
        if theme is None:
            return ((0.45, 0.55, 0.95), (0.55, 0.35, 0.90), (0.75, 0.30, 0.70))
        return tuple(color_to_rgb(color) for color in theme.spectrum_palette)

    def _apply_idle_frame(self) -> None:
        energy = 0.16
        centers = logarithmic_band_centers(self._bar_count, sample_rate=16_000)
        palette = self._palette()
        bars = tuple(energy * (0.55 + 0.2 * ((index % 3) / 2.0)) for index in range(self._bar_count))
        self._bars = bars
        self._peaks = bars
        self._colors = tuple(
            self._boost_alpha(frequency_to_rgba(centers[index], bars[index], palette=palette))
            for index in range(self._bar_count)
        )

    def _apply_live_frame(self, bars: tuple[float, ...]) -> None:
        centers = logarithmic_band_centers(self._bar_count, sample_rate=16_000)
        palette = self._palette()
        peaks = tuple(
            max(bars[index], max(0.0, self._peaks[index] - 0.06))
            for index in range(self._bar_count)
        )
        self._bars = bars
        self._peaks = peaks
        self._colors = tuple(
            self._boost_alpha(frequency_to_rgba(centers[index], bars[index], palette=palette))
            for index in range(self._bar_count)
        )

    def _apply_synthetic_frame(self) -> None:
        t = self._phase
        energy = 0.38 if self._muted else 0.72
        centers = logarithmic_band_centers(self._bar_count, sample_rate=16_000)
        palette = self._palette()
        bars: list[float] = []
        for index in range(self._bar_count):
            wave = abs(math.sin(t * (2.4 + index * 0.55) + index * 1.17))
            pulse = 0.55 + 0.45 * abs(math.sin(t * 1.35 + index * 0.31))
            mid = 1.0 - 0.22 * abs((index / max(1, self._bar_count - 1)) - 0.45)
            level = energy * wave * pulse * mid
            bars.append(max(0.08, min(1.0, level)))
        next_bars = tuple(bars)
        peaks = tuple(
            max(next_bars[index], max(0.0, self._peaks[index] - 0.05))
            for index in range(self._bar_count)
        )
        self._bars = next_bars
        self._peaks = peaks
        self._colors = tuple(
            self._boost_alpha(
                frequency_to_rgba(centers[index], next_bars[index], palette=palette)
            )
            for index in range(self._bar_count)
        )

    @staticmethod
    def _boost_alpha(
        color: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        red, green, blue, alpha = color
        # Soft enough to sit behind icons without washing them out.
        return (red, green, blue, min(0.55, alpha * 1.35 + 0.08))
