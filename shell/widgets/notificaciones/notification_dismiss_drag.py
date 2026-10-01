"""Right-button horizontal drag gesture for dismissing one notification."""

from __future__ import annotations

from collections.abc import Callable
from math import cos, pi

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

RIGHT_BUTTON = 3
DISMISS_DRAG_THRESHOLD = 32


class RightDragDismiss:
    """Turn a right-button horizontal drag into one dismiss callback."""

    def __init__(
        self,
        on_dismiss: Callable[[float], None],
        *,
        threshold: float | Callable[[], float] = DISMISS_DRAG_THRESHOLD,
        on_progress: Callable[[float], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._on_dismiss = on_dismiss
        self._threshold = threshold
        self._on_progress = on_progress or (lambda _progress: None)
        self._on_cancel = on_cancel or (lambda: None)
        self._start: tuple[float, float] | None = None

    def press(self, button: int, x: float, y: float) -> None:
        self._start = (x, y) if button == RIGHT_BUTTON else None

    def motion(self, x: float, y: float) -> float | None:
        if self._start is None:
            return None
        delta_x = x - self._start[0]
        progress = delta_x / self._threshold_px()
        self._on_progress(progress)
        return progress

    def release(self, button: int, x: float, y: float) -> bool:
        start = self._start
        self._start = None
        if start is None:
            return False
        if button != RIGHT_BUTTON:
            self._on_cancel()
            return False

        delta_x = x - start[0]
        delta_y = y - start[1]
        horizontal_intent = abs(delta_x) >= abs(delta_y) * 0.75
        if abs(delta_x) < self._threshold_px() or not horizontal_intent:
            self._on_cancel()
            return False

        self._on_progress(delta_x / DISMISS_DRAG_THRESHOLD)
        self._on_dismiss(delta_x)
        return True

    def _threshold_px(self) -> float:
        value = self._threshold() if callable(self._threshold) else self._threshold
        return max(1.0, float(value))


def connect_right_drag_dismiss(
    widget: Gtk.Widget,
    notification_id: int,
    on_dismiss: Callable[[int], None],
) -> RightDragDismiss:
    animation_tick_id = 0
    drag_origin: tuple[Gdk.Window, int, int] | None = None
    threshold = lambda: max(1.0, widget.get_allocated_width() * 0.5)

    def stop_animation() -> None:
        nonlocal animation_tick_id
        if animation_tick_id:
            widget.remove_tick_callback(animation_tick_id)
            animation_tick_id = 0

    def animate_window(
        target_x: int,
        target_y: int,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        nonlocal animation_tick_id
        stop_animation()
        window = widget.get_window()
        if window is None:
            if on_complete is not None:
                on_complete()
            return
        start_x, start_y = window.get_position()
        started_at = GLib.get_monotonic_time()
        duration_us = 160_000

        def tick(_widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
            nonlocal animation_tick_id
            progress = min(
                1.0,
                (GLib.get_monotonic_time() - started_at) / duration_us,
            )
            eased = 0.5 - 0.5 * cos(pi * progress)
            x = round(start_x + (target_x - start_x) * eased)
            y = round(start_y + (target_y - start_y) * eased)
            window.move(x, y)
            if progress < 1.0:
                return GLib.SOURCE_CONTINUE
            animation_tick_id = 0
            if on_complete is not None:
                on_complete()
            return GLib.SOURCE_REMOVE

        animation_tick_id = widget.add_tick_callback(tick)

    def move_card(progress: float) -> None:
        window_info = drag_origin
        if window_info is None:
            return
        window, start_x, start_y = window_info
        window.move(start_x + round(progress * threshold()), start_y)
        style = widget.get_style_context()
        if progress > 0.0:
            style.add_class("notification-dismiss-dragging")
        elif progress == 0.0:
            style.remove_class("notification-dismiss-dragging")

    def restore_after_cancel() -> None:
        window_info = drag_origin
        if window_info is None:
            widget.get_style_context().remove_class("notification-dismiss-dragging")
            return

        window, start_x, start_y = window_info

        def clear_drag_state() -> None:
            widget.get_style_context().remove_class("notification-dismiss-dragging")

        animate_window(start_x, start_y, clear_drag_state)

    def dismiss_after_slide(delta_x: float) -> None:
        window_info = drag_origin
        if window_info is None:
            on_dismiss(notification_id)
            return
        window, start_x, start_y = window_info
        direction = 1 if delta_x > 0 else -1
        distance = max(widget.get_allocated_width(), DISMISS_DRAG_THRESHOLD) + 24
        target_x = start_x + direction * distance

        def finish_dismiss() -> None:
            widget.get_style_context().remove_class("notification-dismiss-dragging")
            on_dismiss(notification_id)

        animate_window(target_x, start_y, finish_dismiss)

    gesture = RightDragDismiss(
        dismiss_after_slide,
        threshold=threshold,
        on_progress=move_card,
        on_cancel=restore_after_cancel,
    )
    widget.add_events(
        Gdk.EventMask.BUTTON_PRESS_MASK
        | Gdk.EventMask.BUTTON_RELEASE_MASK
        | Gdk.EventMask.POINTER_MOTION_MASK
    )

    def on_press(_widget, event) -> bool:
        nonlocal drag_origin
        stop_animation()
        if event.button == RIGHT_BUTTON:
            window = widget.get_window()
            if window is not None:
                start_x, start_y = window.get_position()
                drag_origin = (window, start_x, start_y)
        gesture.press(event.button, event.x_root, event.y_root)
        return False

    def on_release(_widget, event) -> bool:
        nonlocal drag_origin
        handled = gesture.release(event.button, event.x_root, event.y_root)
        if not handled:
            drag_origin = None
        return handled

    def on_motion(_widget, event) -> bool:
        gesture.motion(event.x_root, event.y_root)
        return False

    widget.connect("button-press-event", on_press)
    widget.connect("button-release-event", on_release)
    widget.connect("motion-notify-event", on_motion)
    return gesture