"""Hovering a grouped parent asks for its group at once."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from shell.widgets.notificaciones.notification_popup import NotificationGroupRow


def test_hover_opens_group_immediately() -> None:
    row = SimpleNamespace()
    row._tools_visible = lambda: False
    row._group_snapshots = ["a", "b"]
    row._hover_opened = False
    row._open_group_window = mock.Mock()
    row._host_popup = lambda: None

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


if __name__ == "__main__":
    test_hover_opens_group_immediately()
    print("ok")
