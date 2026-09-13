"""Short tick-based animation with cubic easing."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

_TICK_MS = 16


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class IslandAnimator:
    """Runs one animation at a time; cancelable without orphan GLib sources."""

    def __init__(self) -> None:
        self._source_id = 0

    @property
    def running(self) -> bool:
        return self._source_id != 0

    def cancel(self) -> None:
        if self._source_id:
            GLib.source_remove(self._source_id)
            self._source_id = 0

    def animate(
        self,
        duration_ms: int,
        on_progress: Callable[[float], None],
        on_done: Callable[[], None] | None = None,
        *,
        easing: Callable[[float], float] = ease_out_cubic,
    ) -> None:
        self.cancel()
        duration = max(1, int(duration_ms))
        started = monotonic()

        def tick() -> bool:
            elapsed_ms = (monotonic() - started) * 1000.0
            raw = min(1.0, elapsed_ms / duration)
            on_progress(easing(raw))
            if raw >= 1.0:
                self._source_id = 0
                if on_done is not None:
                    on_done()
                return False
            return True

        on_progress(easing(0.0))
        self._source_id = GLib.timeout_add(_TICK_MS, tick)
