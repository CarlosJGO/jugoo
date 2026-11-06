from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from shell.models import (
    NOTIFICATION_KIND_ASSISTANT,
    ActiveWindow,
    ApplicationsSnapshot,
    DesktopApplication,
    HyprlandSnapshot,
    NotificationSnapshot,
    Window,
    Workspace,
)
from shell.widgets.notificaciones.notification_activate import sender_on_active_workspace


def _notification(**overrides) -> NotificationSnapshot:
    values = dict(
        id=1,
        app_name="Vesktop",
        app_icon="",
        summary="Nuevo mensaje",
        body="hola",
        actions=(),
        urgency=1,
        timestamp=0.0,
        expire_timeout_ms=-1,
        desktop_entry="vesktop",
    )
    values.update(overrides)
    return NotificationSnapshot(**values)


def _workspace(workspace_id: int, *, active: bool, windows: tuple[Window, ...]) -> Workspace:
    return Workspace(
        id=workspace_id,
        name=str(workspace_id),
        active=active,
        windows=windows,
        icons=(),
        focused_window_address=None,
        is_special=workspace_id < 0,
    )


def _hyprland(
    *,
    active_id: int,
    sender_ws: int,
    focused_address: str = "0xterm",
    special: tuple[Window, ...] = (),
) -> HyprlandSnapshot:
    vesktop = Window(address="0xvesk", app_class="vesktop", title="Discord", workspace_id=sender_ws)
    terminal = Window(address="0xterm", app_class="kitty", title="kitty", workspace_id=1)
    by_ws: dict[int, list[Window]] = {1: [terminal], 2: [], 3: []}
    by_ws.setdefault(sender_ws, []).append(vesktop)
    workspaces = [
        _workspace(ws_id, active=ws_id == active_id, windows=tuple(windows))
        for ws_id, windows in sorted(by_ws.items())
    ]
    if special:
        workspaces.append(_workspace(-98, active=False, windows=special))
    return HyprlandSnapshot(
        workspaces=tuple(workspaces),
        active_window=ActiveWindow(
            address=focused_address, app_class="", application_name="", title="", icon=""
        ),
    )


_APPS = ApplicationsSnapshot(
    applications=(DesktopApplication(id="vesktop", name="Vesktop", icon="", wm_class="vesktop"),)
)


def test_sender_on_active_workspace_is_detected() -> None:
    assert sender_on_active_workspace(
        _notification(), applications=_APPS, hyprland=_hyprland(active_id=1, sender_ws=1)
    )


def test_sender_on_other_workspace_is_not_in_view() -> None:
    assert not sender_on_active_workspace(
        _notification(), applications=_APPS, hyprland=_hyprland(active_id=1, sender_ws=3)
    )


def test_sender_matched_by_app_name_when_not_in_catalog() -> None:
    assert sender_on_active_workspace(
        _notification(desktop_entry=""),
        applications=ApplicationsSnapshot(),
        hyprland=_hyprland(active_id=2, sender_ws=2),
    )


def test_sender_without_window_is_not_in_view() -> None:
    assert not sender_on_active_workspace(
        _notification(app_name="Spotify", desktop_entry="spotify"),
        applications=_APPS,
        hyprland=_hyprland(active_id=1, sender_ws=1),
    )


def test_focused_special_workspace_counts_as_visible() -> None:
    scratch = Window(address="0xscratch", app_class="Spotify", title="Spotify", workspace_id=-98)
    assert sender_on_active_workspace(
        _notification(app_name="Spotify", desktop_entry="spotify"),
        applications=_APPS,
        hyprland=_hyprland(
            active_id=1, sender_ws=3, focused_address="0xscratch", special=(scratch,)
        ),
    )


def test_no_hyprland_snapshot_never_suppresses() -> None:
    assert not sender_on_active_workspace(_notification(), applications=_APPS, hyprland=None)


def _widget(hyprland: HyprlandSnapshot, calls: list[str]):
    from shell.widgets.barra.notifications import NotificationsWidget

    widget = SimpleNamespace(
        _sync_badge=lambda: calls.append("badge"),
        _service=SimpleNamespace(paused=False, should_play_sound=lambda _s: True),
        _animate_bell=lambda: calls.append("bell"),
        _sound_path="/tmp/jugoo-test-notification.ogg",
        _toast_manager=SimpleNamespace(enqueue=lambda _s: calls.append("toast")),
        _shell_window=SimpleNamespace(
            applications=SimpleNamespace(snapshot=_APPS),
            hyprland=SimpleNamespace(snapshot=hyprland),
        ),
    )
    widget._sender_in_view = lambda snapshot: NotificationsWidget._sender_in_view(widget, snapshot)
    return NotificationsWidget, widget


def _receive(snapshot: NotificationSnapshot, hyprland: HyprlandSnapshot, *, enabled=True):
    from shell import config as shell_config

    calls: list[str] = []
    cls, widget = _widget(hyprland, calls)
    with (
        patch.object(shell_config, "NOTIFICATIONS_SOUND_ENABLED", True),
        patch.object(shell_config, "NOTIFICATIONS_TOAST_ENABLED", True),
        patch.object(shell_config, "NOTIFICATIONS_SUPPRESS_ON_ACTIVE_WORKSPACE", enabled),
        patch(
            "shell.widgets.barra.notifications.play_notification_sound",
            side_effect=lambda *_a, **_k: calls.append("sound"),
        ),
    ):
        cls._handle_notification_received(widget, snapshot)
    return calls


def test_handler_skips_toast_and_sound_when_sender_in_view() -> None:
    calls = _receive(_notification(), _hyprland(active_id=1, sender_ws=1))
    assert calls == ["badge"]


def test_handler_presents_when_sender_elsewhere() -> None:
    calls = _receive(_notification(), _hyprland(active_id=1, sender_ws=3))
    assert calls == ["badge", "bell", "sound", "toast"]


def test_handler_presents_when_setting_disabled() -> None:
    calls = _receive(_notification(), _hyprland(active_id=1, sender_ws=1), enabled=False)
    assert calls == ["badge", "bell", "sound", "toast"]


def test_critical_notifications_are_never_suppressed() -> None:
    calls = _receive(_notification(urgency=2), _hyprland(active_id=1, sender_ws=1))
    assert calls == ["badge", "bell", "sound", "toast"]


def test_shell_notifications_are_never_suppressed() -> None:
    from shell.widgets.barra.notifications import NotificationsWidget

    calls: list[str] = []
    _cls, widget = _widget(_hyprland(active_id=1, sender_ws=1), calls)
    for snapshot in (
        _notification(app_name="Jugoo", desktop_entry="com.jugoo.Shell"),
        _notification(kind=NOTIFICATION_KIND_ASSISTANT),
    ):
        assert not NotificationsWidget._sender_in_view(widget, snapshot)


def _run() -> None:
    import inspect

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        assert not inspect.signature(test).parameters
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
