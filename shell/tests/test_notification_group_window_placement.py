"""Regression tests for notification group panel placement."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from shell.widgets.notificaciones import notification_group_window as module


def test_shrinking_panel_preserves_top_position_for_bottom_only_crop() -> None:
    window = mock.Mock()
    window._width = 360
    window._window_height = 556
    window._placed_x = 100
    window._placed_y = 220
    window._placed_height = 556
    window._notifications_position = (100, 180, 360)
    window.get_size_request.return_value = SimpleNamespace(height=556)
    window._resolve_parent_popup_rect.return_value = (100, 180, 360)
    window._panel_vertical_bounds.return_value = (180, 1000)
    window._fit_window_height.return_value = 200
    window._clamp_y = module.NotificationGroupWindow._clamp_y

    monitor = SimpleNamespace(x=0, y=0, width=1280, height=800)
    with mock.patch.object(
        module, "monitor_containing_point", return_value=monitor
    ), mock.patch.object(
        module, "anchor_button_geometry", return_value=SimpleNamespace(
            top=400, height=40
        )
    ):
        placement = module.NotificationGroupWindow._compute_placement(
            window,
            mock.Mock(),
            mock.Mock(),
        )

    assert placement[1] == 220
    assert window._fit_window_height.call_args.args == (180, 1000)


if __name__ == "__main__":
    test_shrinking_panel_preserves_top_position_for_bottom_only_crop()
    print("ok")
