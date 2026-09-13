"""Organic Islands state machine and easing helpers."""

from __future__ import annotations

from shell.ui.islands.animator import ease_out_cubic, lerp
from shell.ui.islands.states import IslandState


def test_island_state_flags() -> None:
    assert IslandState.ATTACHED.is_busy is False
    assert IslandState.ATTACHED.is_detached is False
    assert IslandState.DETACHING.is_busy is True
    assert IslandState.DETACHING.is_detached is True
    assert IslandState.ACTIVE.is_busy is False
    assert IslandState.ACTIVE.is_detached is True
    assert IslandState.RETURNING.is_busy is True


def test_ease_out_cubic_bounds() -> None:
    assert ease_out_cubic(0.0) == 0.0
    assert ease_out_cubic(1.0) == 1.0
    assert 0.0 < ease_out_cubic(0.5) < 1.0
    assert ease_out_cubic(0.5) > 0.5


def test_lerp() -> None:
    assert lerp(10, 20, 0.0) == 10
    assert lerp(10, 20, 1.0) == 20
    assert lerp(10, 20, 0.5) == 15


if __name__ == "__main__":
    test_island_state_flags()
    test_ease_out_cubic_bounds()
    test_lerp()
    print("islands tests OK")
