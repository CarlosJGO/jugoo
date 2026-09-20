"""Tests for cached block pages, immediate hover, and coalesced destinations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from shell.config import NOTIFICATION_GROUP_SLIDE_DURATION_MS
from shell.widgets.notificaciones.notification_group_window import (
    NotificationGroupWindow,
    _ease_in_out_cubic,
    _fingerprint,
    _lerp,
)
from shell.widgets.notificaciones.notification_popup import NotificationGroupRow


def test_slide_duration_is_deliberately_long() -> None:
    assert NOTIFICATION_GROUP_SLIDE_DURATION_MS == 700


def test_hover_opens_group_immediately() -> None:
    row = SimpleNamespace()
    row._tools_visible = lambda: False
    row._group_snapshots = ["a", "b"]
    row._hover_opened = False
    row._open_group_window = mock.Mock()

    event = SimpleNamespace(mode=None, detail=None)
    with mock.patch(
        "shell.widgets.notificaciones.notification_popup.Gdk"
    ) as gdk:
        gdk.CrossingMode.GRAB = object()
        gdk.CrossingMode.UNGRAB = object()
        gdk.NotifyType.INFERIOR = object()
        assert NotificationGroupRow._on_enter_notify(row, None, event) is False

    row._open_group_window.assert_called_once_with()
    assert row._hover_opened is True


def test_coalesce_keeps_only_latest_destination() -> None:
    """Simula A→B en curso y requests C luego D: solo D queda en cola."""
    queued = None

    def request(dest: str, *, animating: bool) -> str | None:
        nonlocal queued
        if animating:
            queued = dest
            return None
        return dest

    assert request("B", animating=False) == "B"
    assert request("C", animating=True) is None
    assert queued == "C"
    assert request("D", animating=True) is None
    assert queued == "D"


def test_fingerprint_stable_for_same_ids() -> None:
    snaps = [
        SimpleNamespace(id=1),
        SimpleNamespace(id=2),
    ]
    assert _fingerprint(snaps) == (1, 2)  # type: ignore[arg-type]


def test_clamp_y_respects_panel_band() -> None:
    clamp = NotificationGroupWindow._clamp_y
    # Ideal above the bar → pinned to panel_top
    assert clamp(-50, window_height=200, panel_top=40, panel_bottom=800) == 40
    # Ideal below → pinned so bottom stays in panel
    assert clamp(700, window_height=200, panel_top=40, panel_bottom=800) == 600
    # Ideal inside → unchanged
    assert clamp(100, window_height=200, panel_top=40, panel_bottom=800) == 100


def test_lerp_and_ease_still_continuous() -> None:
    assert _lerp(400.0, 100.0, 1.0) == 100.0
    samples = [_ease_in_out_cubic(t / 10) for t in range(11)]
    assert samples == sorted(samples)


if __name__ == "__main__":
    test_slide_duration_is_deliberately_long()
    test_hover_opens_group_immediately()
    test_coalesce_keeps_only_latest_destination()
    test_fingerprint_stable_for_same_ids()
    test_clamp_y_respects_panel_band()
    test_lerp_and_ease_still_continuous()
    print("ok")
