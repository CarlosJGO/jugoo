from __future__ import annotations

from types import SimpleNamespace

import shell.ui.door as door


def test_door_easing_is_symmetric_and_monotonic() -> None:
    samples = [door._ease(index / 20) for index in range(21)]

    assert samples[0] == 0.0
    assert samples[-1] == 1.0
    assert abs(samples[10] - 0.5) < 1e-9
    assert all(left <= right for left, right in zip(samples, samples[1:]))
    assert abs(samples[5] + samples[15] - 1.0) < 1e-9


def test_door_duration_gives_launcher_panel_more_frames() -> None:
    previous_theme_provider = door.active_theme
    try:
        door.active_theme = lambda: SimpleNamespace(
            animation=SimpleNamespace(enabled=True, duration=160)
        )
        assert door._animation_duration_ms() == 320
        door.active_theme = lambda: SimpleNamespace(
            animation=SimpleNamespace(enabled=True, duration=180)
        )
        assert door._animation_duration_ms() == 360
        door.active_theme = lambda: SimpleNamespace(
            animation=SimpleNamespace(enabled=True, duration=1000)
        )
        assert door._animation_duration_ms() == 420
        door.active_theme = lambda: SimpleNamespace(
            animation=SimpleNamespace(enabled=False, duration=180)
        )
        assert door._animation_duration_ms() == 0
    finally:
        door.active_theme = previous_theme_provider


def _run() -> None:
    import inspect

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        assert not inspect.signature(test).parameters
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()