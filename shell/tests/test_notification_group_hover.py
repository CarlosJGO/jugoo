"""Tests for delayed opening of grouped notification windows."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from shell.config import NOTIFICATION_GROUP_HOVER_DELAY_MS
from shell.widgets.notificaciones.notification_popup import NotificationGroupRow


def _row() -> SimpleNamespace:
    row = SimpleNamespace()
    row._group_hover_timeout_id = 0
    row._hover_opened = False
    row._open_group_window = mock.Mock()
    row._open_group_window_after_hover = (
        lambda: NotificationGroupRow._open_group_window_after_hover(row)
    )
    return row


def test_group_window_opens_only_after_hover_delay() -> None:
    row = _row()
    callback = None

    def timeout_add(delay: int, scheduled_callback):
        assert delay == NOTIFICATION_GROUP_HOVER_DELAY_MS
        nonlocal callback
        callback = scheduled_callback
        return 17

    with mock.patch(
        "shell.widgets.notificaciones.notification_popup.GLib.timeout_add",
        side_effect=timeout_add,
    ):
        NotificationGroupRow._schedule_group_window_open(row)

    row._open_group_window.assert_not_called()
    assert row._group_hover_timeout_id == 17
    assert callback is not None

    assert callback() is False
    row._open_group_window.assert_called_once_with()
    assert row._hover_opened is True
    assert row._group_hover_timeout_id == 0


def test_leaving_group_row_cancels_pending_open() -> None:
    row = _row()
    row._group_hover_timeout_id = 23

    with mock.patch(
        "shell.widgets.notificaciones.notification_popup.GLib.source_remove"
    ) as source_remove:
        NotificationGroupRow._cancel_group_window_open(row)

    source_remove.assert_called_once_with(23)
    assert row._group_hover_timeout_id == 0
    row._open_group_window.assert_not_called()


if __name__ == "__main__":
    test_group_window_opens_only_after_hover_delay()
    test_leaving_group_row_cancels_pending_open()
    print("ok")