from __future__ import annotations

from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from shell.identity import APPLICATION_NAME
from shell.popup_spawn import (
    SpawnMonitor,
    SpawnPlacement,
    monitors_from_hyprctl_payload,
    placement_for_anchor,
    read_spawn_payload,
    write_spawn_payload,
)


def test_monitors_from_hyprctl_payload_skips_invalid_entries() -> None:
    monitors = monitors_from_hyprctl_payload(
        [
            {"name": "DP-1", "x": 0, "y": 0, "width": 1920, "height": 1080},
            {"name": "broken"},
            {"name": "HDMI-A-1", "x": 1920, "y": 0, "width": 2560, "height": 1440},
        ]
    )
    assert len(monitors) == 2
    assert monitors[0].name == "DP-1"
    assert monitors[1].x == 1920


def test_placement_for_anchor_is_monitor_local() -> None:
    monitors = (
        SpawnMonitor(name="HDMI-A-1", x=1920, y=0, width=2560, height=1440),
    )
    placement = placement_for_anchor(
        title=f"{APPLICATION_NAME} Clock Calendar",
        button_center_x=1920 + 200,
        button_bottom=40,
        popup_width=320,
        popup_height=400,
        offset=8,
        monitors=monitors,
    )
    assert placement is not None
    assert placement.x == 1920 + 200 - 160
    assert placement.y == 48
    assert placement.local_x == 200 - 160
    assert placement.local_y == 48
    assert placement.monitor == "HDMI-A-1"


def test_write_spawn_payload_merges_titles(tmp_path: Path) -> None:
    path = tmp_path / "popup-spawn.json"
    first = SpawnPlacement(
        title="Jugoo Clock Calendar",
        x=10,
        y=20,
        local_x=10,
        local_y=20,
        monitor="DP-1",
    )
    second = SpawnPlacement(
        title="Jugoo Tasks",
        x=30,
        y=40,
        local_x=30,
        local_y=40,
        monitor="DP-1",
    )
    write_spawn_payload(first, path=path)
    write_spawn_payload(second, path=path)
    payload = read_spawn_payload(path)
    assert payload["Jugoo Clock Calendar"]["x"] == 10
    assert payload["Jugoo Tasks"]["y"] == 40
    updated = SpawnPlacement(
        title="Jugoo Clock Calendar",
        x=99,
        y=20,
        local_x=10,
        local_y=20,
        monitor="DP-1",
    )
    write_spawn_payload(updated, path=path)
    payload = read_spawn_payload(path)
    assert payload["Jugoo Clock Calendar"]["x"] == 99
    assert payload["Jugoo Tasks"]["x"] == 30


def test_spawn_json_is_readable_by_lua_pattern(tmp_path: Path) -> None:
    path = tmp_path / "popup-spawn.json"
    write_spawn_payload(
        SpawnPlacement(
            title="Jugoo Power Menu",
            x=100,
            y=48,
            local_x=100,
            local_y=48,
            monitor="DP-1",
        ),
        path=path,
    )
    body = path.read_text(encoding="utf-8")
    assert '"Jugoo Power Menu"' in body
    assert '"local_x": 100' in body
    assert '"monitor": "DP-1"' in body


def test_notify_hyprland_spawn_reload_is_eval_not_move() -> None:
    from shell import popup_spawn

    with mock.patch("shell.popup_spawn.subprocess.run") as run:
        popup_spawn.notify_hyprland_spawn_reload()
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args[:2] == ["hyprctl", "eval"]
    assert "jugoo_reload_popup_spawns()" in args
    joined = " ".join(args)
    assert "window.move" not in joined


def _run() -> None:
    import inspect
    import tempfile

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        parameters = inspect.signature(test).parameters
        if "tmp_path" in parameters:
            with tempfile.TemporaryDirectory() as folder:
                test(Path(folder))
        else:
            test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
