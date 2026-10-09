"""Geometry and state machine of the grouped-notifications card (no GTK).

The card is a separate floating window kept right under the parent popup.
It rests on a hidden rect inside the parent and Hyprland slides it sideways
to a visible rect with its ``windowsMove`` animation. Content and geometry
only change while it rests hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Hashable

from ...ui.door import symmetric_ease

DIRECTION_AUTO = "auto"
DIRECTION_LEFT = "left"
DIRECTION_RIGHT = "right"

CURVE_COSINE = "cosine"
CURVE_EASE_OUT = "ease_out"
CURVE_LINEAR = "linear"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


@dataclass(frozen=True)
class DeckTrack:
    """Where the card rests (``hidden_x``) and where it shows (``visible_x``)."""

    hidden_x: int
    visible_x: int
    y: int
    width: int
    height: int


def deck_track(
    parent: Rect,
    monitor: Rect | None,
    *,
    inset: int,
    overlap: int,
    direction: str = DIRECTION_AUTO,
) -> DeckTrack:
    """The card is the parent minus ``inset`` on every side, so at rest no
    edge of it can show around the parent. It shows beside the parent with
    ``overlap`` px still tucked under it.

    ``auto`` prefers the left and switches sides when the card does not fit
    on the parent's monitor; if it fits on neither side it takes the roomier
    one and the monitor edge clips it.
    """
    width = max(1, parent.width - 2 * inset)
    height = max(1, parent.height - 2 * inset)
    hidden_x = parent.x + inset
    left_x = parent.x - width + overlap
    right_x = parent.right - overlap
    side = direction
    if side not in (DIRECTION_LEFT, DIRECTION_RIGHT):
        side = DIRECTION_LEFT
        if monitor is not None:
            room_left = parent.x - monitor.x
            room_right = monitor.right - parent.right
            needed = width - overlap
            if room_left < needed and room_right > room_left:
                side = DIRECTION_RIGHT
    visible_x = left_x if side == DIRECTION_LEFT else right_x
    return DeckTrack(
        hidden_x=hidden_x,
        visible_x=visible_x,
        y=parent.y + inset,
        width=width,
        height=height,
    )


def ease(curve: str, t: float) -> float:
    t = max(0.0, min(1.0, t))
    if curve == CURVE_LINEAR:
        return t
    if curve == CURVE_EASE_OUT:
        return 1.0 - (1.0 - t) ** 3
    return symmetric_ease(t)


class HoverRegion:
    """Whether the pointer is over the popup ∪ the card, as one region.

    Fed with pointer samples. Coming in counts at once; going out only once
    it lasts ``tolerance_ms``, so a brush past the edge does not count.
    """

    def __init__(self, tolerance_ms: Callable[[], int]) -> None:
        self._tolerance_ms = tolerance_ms
        self.inside = False
        self._outside_since: float | None = None

    def sample(self, inside: bool, now_ms: float) -> bool | None:
        """Returns the new value when it settles on a change, else ``None``."""
        if inside:
            self._outside_since = None
            if self.inside:
                return None
            self.inside = True
            return True
        if not self.inside:
            return None
        if self._outside_since is None:
            self._outside_since = now_ms
        if now_ms - self._outside_since < max(0, self._tolerance_ms()):
            return None
        self.inside = False
        self._outside_since = None
        return False

    def reset(self) -> None:
        self.inside = False
        self._outside_since = None


class DeckState(Enum):
    HIDDEN = "hidden"
    ENTERING = "entering"
    VISIBLE = "visible"
    EXITING = "exiting"


@dataclass(frozen=True)
class DeckMotion:
    duration_ms: int
    # Only estimates where Hyprland has the card; its bezier draws it.
    curve: str = CURVE_EASE_OUT
    reduced: bool = False


class DeckMachine:
    """Which content the card shows and where it is along its track.

    ``want(key)`` names the content the pointer asks for (``None`` = none).
    Only the latest wish counts. A different key while the card is out sends
    it back first; the new content is loaded only once it rests hidden.

    * ``load(key)`` prepares content and track for ``key`` while hidden and
      answers with ``loaded(key)`` once it is painted in place;
    * ``track_for(key)`` gives that key's track;
    * ``move(x, animate)`` sends the card to ``x`` once per transition;
      Hyprland animates it, or it jumps when motion is reduced;
    * ``request_ticks()`` asks for ``tick(now_ms)`` once the transition's
      time is up (``remaining_ms``); Hyprland gives no end-of-move event, so
      the duration matches its ``windowsMove`` animation.
    """

    def __init__(
        self,
        *,
        load: Callable[[Hashable], None],
        track_for: Callable[[Hashable], DeckTrack],
        move: Callable[[int, bool], None],
        request_ticks: Callable[[], None],
        motion: Callable[[], DeckMotion],
    ) -> None:
        self._load = load
        self._track_for = track_for
        self._move = move
        self._request_ticks = request_ticks
        self._motion = motion
        self.state = DeckState.HIDDEN
        self.shown: Hashable | None = None
        self.wanted: Hashable | None = None
        self.loading: Hashable | None = None
        self.track: DeckTrack | None = None
        self.x: int | None = None
        self._from_x = 0
        self._to_x = 0
        self._started_ms: float | None = None
        self._duration_ms = 0

    @property
    def animating(self) -> bool:
        return self.state in (DeckState.ENTERING, DeckState.EXITING)

    def want(self, key: Hashable | None, now_ms: float) -> None:
        self.wanted = key
        self._advance(now_ms)

    def loaded(self, key: Hashable, now_ms: float) -> None:
        if key != self.loading:
            return
        self.loading = None
        if self.state is not DeckState.HIDDEN:
            return
        self.shown = key
        self.track = self._track_for(key)
        self.x = self.track.hidden_x
        self._advance(now_ms)

    def card_rect(self) -> Rect | None:
        """Where the card may be now, or ``None`` while it rests hidden.

        While Hyprland slides it, the whole path counts: its curve is not
        the one estimated here.
        """
        if self.state is DeckState.HIDDEN or self.track is None or self.x is None:
            return None
        if self.animating:
            left = min(self._from_x, self._to_x)
            width = abs(self._to_x - self._from_x) + self.track.width
            return Rect(left, self.track.y, width, self.track.height)
        return Rect(self.x, self.track.y, self.track.width, self.track.height)

    def unload(self) -> None:
        """The resting card was unmapped: whatever comes next loads afresh."""
        if self.state is not DeckState.HIDDEN:
            return
        self.shown = None
        self.loading = None
        self.track = None
        self.x = None

    def reset(self) -> None:
        """The window went away (popup closed): forget everything."""
        self.state = DeckState.HIDDEN
        self.shown = None
        self.wanted = None
        self.loading = None
        self.track = None
        self.x = None
        self._started_ms = None

    def remaining_ms(self, now_ms: float) -> float:
        if not self.animating or self._started_ms is None:
            return 0.0
        return max(0.0, self._started_ms + self._duration_ms - now_ms)

    def tick(self, now_ms: float) -> bool:
        """Follows the compositor's slide; settles once its time is up."""
        if not self.animating:
            return False
        if self._follow(now_ms):
            return True
        self.x = self._to_x
        self.state = (
            DeckState.VISIBLE if self.state is DeckState.ENTERING else DeckState.HIDDEN
        )
        self._started_ms = None
        self._advance(now_ms)
        return self.animating

    def _follow(self, now_ms: float) -> bool:
        """Estimates ``x`` mid-slide; ``False`` once the slide is over."""
        assert self._started_ms is not None
        elapsed = now_ms - self._started_ms
        t = 1.0 if self._duration_ms <= 0 else elapsed / self._duration_ms
        if t >= 1.0:
            return False
        motion = self._motion()
        self.x = round(self._from_x + (self._to_x - self._from_x) * ease(motion.curve, t))
        return True

    def _advance(self, now_ms: float) -> None:
        wanted = self.wanted
        if self.state is DeckState.HIDDEN:
            if wanted is None:
                return
            if wanted == self.shown and self.loading is None:
                self._start(DeckState.ENTERING, now_ms)
            elif self.loading != wanted:
                self.loading = wanted
                self._load(wanted)
            return
        if self.state in (DeckState.ENTERING, DeckState.VISIBLE):
            if wanted != self.shown:
                self._start(DeckState.EXITING, now_ms)
            return
        if self.state is DeckState.EXITING and wanted == self.shown:
            self._start(DeckState.ENTERING, now_ms)

    def _start(self, state: DeckState, now_ms: float) -> None:
        assert self.track is not None
        motion = self._motion()
        target = self.track.visible_x if state is DeckState.ENTERING else self.track.hidden_x
        if self.animating:
            self._follow(now_ms)
        start = self.x if self.x is not None else self.track.hidden_x
        self.state = state
        self._from_x = start
        self._to_x = target
        self._started_ms = now_ms
        # Hyprland restarts its animation from where the card is, with the
        # whole duration, also on a reversal halfway.
        self._duration_ms = 0 if motion.reduced else int(motion.duration_ms)
        self._move(target, not motion.reduced)
        self._request_ticks()
