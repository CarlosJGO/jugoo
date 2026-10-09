"""Grouped-notifications card: geometry and state machine (no GTK)."""

from __future__ import annotations

from shell.widgets.notificaciones.notification_deck_motion import (
    CURVE_COSINE,
    CURVE_EASE_OUT,
    CURVE_LINEAR,
    DIRECTION_LEFT,
    DIRECTION_RIGHT,
    DeckMachine,
    DeckMotion,
    DeckState,
    DeckTrack,
    Rect,
    deck_track,
    ease,
)

PARENT = Rect(1500, 50, 360, 400)
MONITOR = Rect(0, 0, 1920, 1080)
TRACK = DeckTrack(hidden_x=100, visible_x=-200, y=10, width=300, height=200)


class _Card:
    """Records what the machine asks of the window."""

    def __init__(self, motion: DeckMotion | None = None) -> None:
        self.loads: list[object] = []
        self.moves: list[tuple[int, bool]] = []
        self.tick_requests = 0
        self.motion = motion or DeckMotion(duration_ms=200)
        self.machine = DeckMachine(
            load=self.loads.append,
            track_for=lambda _key: TRACK,
            move=lambda x, animate: self.moves.append((x, animate)),
            request_ticks=self._request,
            motion=lambda: self.motion,
        )

    def _request(self) -> None:
        self.tick_requests += 1

    def run(self, start_ms: float, until_ms: float, step_ms: float = 10) -> float:
        now = start_ms
        while now <= until_ms and self.machine.tick(now):
            now += step_ms
        return now


def test_hidden_rect_sits_inside_the_parent_on_every_side() -> None:
    track = deck_track(PARENT, MONITOR, inset=6, overlap=18)
    assert track.hidden_x == PARENT.x + 6
    assert track.y == PARENT.y + 6
    assert track.hidden_x + track.width == PARENT.right - 6
    assert track.y + track.height == PARENT.bottom - 6


def test_card_shows_left_with_the_overlap_tucked_under_the_parent() -> None:
    track = deck_track(PARENT, MONITOR, inset=6, overlap=18)
    assert track.visible_x + track.width == PARENT.x + 18
    assert track.y == PARENT.y + 6


def test_auto_switches_to_the_right_when_the_left_has_no_room() -> None:
    near_left = Rect(100, 50, 360, 400)
    track = deck_track(near_left, MONITOR, inset=6, overlap=18)
    assert track.visible_x == near_left.right - 18


def test_auto_keeps_the_roomier_side_when_neither_fits() -> None:
    narrow = Rect(0, 0, 700, 1080)
    parent = Rect(200, 50, 360, 400)
    track = deck_track(parent, narrow, inset=6, overlap=18)
    assert track.visible_x == parent.x - track.width + 18


def test_room_is_measured_on_the_parents_own_monitor() -> None:
    second = Rect(1920, 0, 2560, 1440)
    near_its_left = Rect(1950, 40, 360, 400)
    track = deck_track(near_its_left, second, inset=6, overlap=18)
    # Room on the first monitor does not count: the card would straddle both.
    assert track.visible_x == near_its_left.right - 18
    assert track.visible_x + track.width <= second.right
    middle = Rect(3000, 40, 360, 400)
    track = deck_track(middle, second, inset=6, overlap=18)
    assert track.visible_x >= second.x


def test_odd_logical_sizes_keep_the_card_inside_the_parent() -> None:
    # Fractional scaling hands out odd logical sizes.
    parent = Rect(1207, 55, 361, 547)
    track = deck_track(parent, MONITOR, inset=6, overlap=18)
    assert track.hidden_x >= parent.x and track.hidden_x + track.width <= parent.right
    assert track.y >= parent.y and track.y + track.height <= parent.bottom


def test_explicit_direction_wins_over_room() -> None:
    near_left = Rect(100, 50, 360, 400)
    left = deck_track(near_left, MONITOR, inset=6, overlap=18, direction=DIRECTION_LEFT)
    right = deck_track(PARENT, MONITOR, inset=6, overlap=18, direction=DIRECTION_RIGHT)
    assert left.visible_x < near_left.x
    assert right.visible_x == PARENT.right - 18


def test_curves_start_at_zero_and_end_at_one() -> None:
    for curve in (CURVE_COSINE, CURVE_EASE_OUT, CURVE_LINEAR):
        assert ease(curve, 0.0) == 0.0
        assert abs(ease(curve, 1.0) - 1.0) < 1e-9
        assert ease(curve, -1.0) == 0.0 and ease(curve, 2.0) == 1.0


def test_first_request_loads_while_hidden_then_slides_out() -> None:
    card = _Card()
    card.machine.want("a", 0)
    assert card.loads == ["a"]
    assert card.machine.state is DeckState.HIDDEN
    assert card.moves == []

    card.machine.loaded("a", 0)
    assert card.machine.state is DeckState.ENTERING
    # One order: Hyprland animates the whole way.
    assert card.moves == [(TRACK.visible_x, True)]
    assert card.machine.remaining_ms(50) == 150
    assert card.machine.tick(199) is True
    assert card.machine.tick(200) is False
    assert card.machine.state is DeckState.VISIBLE
    assert card.moves == [(TRACK.visible_x, True)]


def test_card_rect_covers_the_whole_path_while_sliding() -> None:
    card = _Card()
    card.machine.want("a", 0)
    card.machine.loaded("a", 0)
    card.machine.tick(100)
    rect = card.machine.card_rect()
    assert rect == Rect(TRACK.visible_x, TRACK.y, TRACK.width + 300, TRACK.height)
    card.run(100, 1000)
    assert card.machine.card_rect() == Rect(TRACK.visible_x, TRACK.y, TRACK.width, TRACK.height)


def test_new_group_waits_until_the_card_is_hidden() -> None:
    card = _Card()
    card.machine.want("a", 0)
    card.machine.loaded("a", 0)
    card.run(0, 1000)

    card.machine.want("b", 1000)
    assert card.machine.state is DeckState.EXITING
    assert card.loads == ["a"]
    card.run(1000, 2000)
    assert card.machine.state is DeckState.HIDDEN
    assert card.moves[-1] == (TRACK.hidden_x, True)
    assert card.loads == ["a", "b"]


def test_only_the_latest_request_is_loaded() -> None:
    card = _Card()
    card.machine.want("a", 0)
    card.machine.loaded("a", 0)
    card.run(0, 1000)

    card.machine.want("b", 1000)
    card.machine.want("c", 1010)
    card.machine.want("d", 1020)
    card.run(1000, 2000)
    assert card.loads == ["a", "d"]

    card.machine.loaded("b", 2000)
    assert card.machine.state is DeckState.HIDDEN
    card.machine.loaded("d", 2000)
    assert card.machine.state is DeckState.ENTERING
    assert card.machine.shown == "d"


def test_returning_to_the_same_group_reverses_without_reloading() -> None:
    card = _Card()
    card.machine.want("a", 0)
    card.machine.loaded("a", 0)
    card.run(0, 1000)

    card.machine.want(None, 1000)
    card.machine.want("a", 1080)
    halfway = card.machine.x
    assert TRACK.visible_x < halfway < TRACK.hidden_x
    assert card.machine.state is DeckState.ENTERING
    assert card.moves[-2:] == [(TRACK.hidden_x, True), (TRACK.visible_x, True)]
    # Hyprland starts over from where the card is, with the whole duration.
    assert card.machine.remaining_ms(1080) == 200
    card.run(1080, 3000)
    assert card.machine.state is DeckState.VISIBLE
    assert card.loads == ["a"]


def test_release_while_loading_keeps_the_card_hidden() -> None:
    card = _Card()
    card.machine.want("a", 0)
    card.machine.want(None, 5)
    card.machine.loaded("a", 10)
    assert card.machine.state is DeckState.HIDDEN
    assert card.moves == []


def test_reduced_motion_jumps_to_the_end() -> None:
    card = _Card(DeckMotion(duration_ms=200, reduced=True))
    card.machine.want("a", 0)
    card.machine.loaded("a", 0)
    assert card.moves == [(TRACK.visible_x, False)]
    assert card.machine.tick(0) is False
    assert card.machine.state is DeckState.VISIBLE


def test_reset_forgets_the_shown_group() -> None:
    card = _Card()
    card.machine.want("a", 0)
    card.machine.loaded("a", 0)
    card.run(0, 1000)
    card.machine.reset()
    assert card.machine.state is DeckState.HIDDEN
    card.machine.want("a", 2000)
    assert card.loads == ["a", "a"]


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
    print("ok")
