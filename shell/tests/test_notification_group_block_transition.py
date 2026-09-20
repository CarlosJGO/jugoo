"""Unit tests for block-page helpers (cache-friendly transition math)."""

from __future__ import annotations

from unittest import mock

from shell.widgets.notificaciones.notification_group_window import (
    _ease_in_out_cubic,
    _lerp,
    _measure_page_height,
)


def test_lerp_endpoints_and_midpoint() -> None:
    assert _lerp(100.0, 200.0, 0.0) == 100.0
    assert _lerp(100.0, 200.0, 1.0) == 200.0
    assert _lerp(100.0, 200.0, 0.5) == 150.0
    assert _lerp(400.0, 180.0, 0.0) == 400.0
    assert _lerp(400.0, 180.0, 1.0) == 180.0


def test_ease_is_smooth_and_bounded() -> None:
    assert _ease_in_out_cubic(0.0) == 0.0
    assert _ease_in_out_cubic(1.0) == 1.0
    samples = [_ease_in_out_cubic(t / 10) for t in range(11)]
    assert samples == sorted(samples)


def test_measure_page_height_sums_children_and_spacing() -> None:
    spacing = 8
    child_heights = (40, 60, 50)
    children = []
    for height in child_heights:
        child = mock.Mock()
        child.set_size_request = mock.Mock()
        child.get_preferred_height_for_width = mock.Mock(
            return_value=(height, height)
        )
        children.append(child)
    page_box = mock.Mock()
    page_box.get_children = mock.Mock(return_value=children)
    total = _measure_page_height(page_box, width=360, spacing=spacing)
    expected = sum(child_heights) + spacing * (len(child_heights) - 1)
    assert total == expected


def test_measure_page_height_empty_is_one() -> None:
    page_box = mock.Mock()
    page_box.get_children = mock.Mock(return_value=[])
    assert _measure_page_height(page_box, width=360, spacing=8) == 1


if __name__ == "__main__":
    test_lerp_endpoints_and_midpoint()
    test_ease_is_smooth_and_bounded()
    test_measure_page_height_sums_children_and_spacing()
    test_measure_page_height_empty_is_one()
    print("ok")
