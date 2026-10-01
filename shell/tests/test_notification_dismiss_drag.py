from __future__ import annotations

from types import SimpleNamespace

from shell.widgets.notificaciones.notification_dismiss_drag import RightDragDismiss
from shell.widgets.notificaciones.notification_dismiss_drag import (
    connect_right_drag_dismiss,
)


def test_right_drag_dismisses_in_both_horizontal_directions() -> None:
    dismissed: list[float] = []
    gesture = RightDragDismiss(dismissed.append)

    gesture.press(3, 100, 100)
    assert gesture.release(3, 40, 106) is True
    gesture.press(3, 100, 100)
    assert gesture.release(3, 160, 95) is True
    assert dismissed == [-60, 60]


def test_right_drag_ignores_clicks_vertical_motion_and_other_buttons() -> None:
    dismissed: list[str] = []
    gesture = RightDragDismiss(lambda _delta: dismissed.append("dismissed"))

    gesture.press(3, 100, 100)
    assert gesture.release(3, 120, 100) is False
    gesture.press(3, 100, 100)
    assert gesture.release(3, 110, 170) is False
    gesture.press(1, 100, 100)
    assert gesture.release(1, 170, 100) is False
    gesture.press(3, 100, 100)
    assert gesture.release(1, 170, 100) is False
    assert dismissed == []


def test_right_drag_reports_progress_and_returns_when_cancelled() -> None:
    progress: list[float] = []
    cancelled: list[bool] = []
    dismissed: list[bool] = []
    gesture = RightDragDismiss(
        lambda _delta: dismissed.append(True),
        on_progress=progress.append,
        on_cancel=lambda: cancelled.append(True),
    )

    gesture.press(3, 100, 100)
    assert gesture.motion(124, 103) == 0.75
    assert gesture.release(3, 124, 103) is False
    assert progress[-1] == 0.75
    assert cancelled == [True]
    assert dismissed == []

    gesture.press(3, 100, 100)
    assert gesture.motion(132, 102) == 1.0
    assert gesture.release(3, 160, 102) is True
    assert dismissed == [True]


def test_diagonal_drag_is_accepted_with_horizontal_intent() -> None:
    dismissed: list[float] = []
    gesture = RightDragDismiss(dismissed.append)

    gesture.press(3, 100, 100)
    assert gesture.release(3, 140, 150) is True
    assert dismissed == [40]


def test_dismiss_threshold_can_match_half_the_card_width() -> None:
    dismissed: list[float] = []
    card_width = 360
    gesture = RightDragDismiss(
        dismissed.append,
        threshold=lambda: card_width / 2,
    )

    gesture.press(3, 0, 0)
    assert gesture.release(3, 179, 0) is False
    gesture.press(3, 0, 0)
    assert gesture.release(3, 180, 0) is True
    assert dismissed == [180]


def test_widget_moves_with_drag_without_dismissing_any_notification() -> None:
    class FakeWindow:
        def __init__(self) -> None:
            self.position = (10, 20)

        def get_position(self) -> tuple[int, int]:
            return self.position

        def move(self, x: int, y: int) -> None:
            self.position = (x, y)

    class FakeStyle:
        def __init__(self) -> None:
            self.classes: set[str] = set()

        def add_class(self, name: str) -> None:
            self.classes.add(name)

        def remove_class(self, name: str) -> None:
            self.classes.discard(name)

    class FakeWidget:
        def __init__(self) -> None:
            self.window = FakeWindow()
            self.style = FakeStyle()
            self.handlers = {}

        def add_events(self, _events) -> None:
            pass

        def connect(self, name, callback) -> None:
            self.handlers[name] = callback

        def get_window(self) -> FakeWindow:
            return self.window

        def get_style_context(self) -> FakeStyle:
            return self.style

        def remove_tick_callback(self, _tick_id: int) -> None:
            pass

        def add_tick_callback(self, _callback) -> int:
            return 1

        def get_allocated_width(self) -> int:
            return 360

    dismissed: list[int] = []
    widget = FakeWidget()
    connect_right_drag_dismiss(widget, 42, dismissed.append)
    widget.handlers["button-press-event"](
        widget,
        SimpleNamespace(button=3, x_root=100, y_root=100),
    )
    widget.handlers["motion-notify-event"](
        widget,
        SimpleNamespace(x_root=124, y_root=104),
    )

    assert widget.window.position == (34, 20)
    assert "notification-dismiss-dragging" in widget.style.classes
    assert dismissed == []


def _run() -> None:
    import inspect

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        assert not inspect.signature(test).parameters
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()