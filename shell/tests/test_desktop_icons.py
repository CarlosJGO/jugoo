"""Desktop icons store, service, and safe opener (no GTK required)."""

from __future__ import annotations

import json
from pathlib import Path

from shell.eventbus import EventBus
from shell.servicios.escritorio.desktop_icons.model import DesktopShortcut, new_shortcut_id
from shell.servicios.escritorio.desktop_icons.opener import DesktopOpenError, open_shortcut
from shell.servicios.escritorio.desktop_icons.service import DesktopIconsService
from shell.servicios.escritorio.desktop_icons.store import load_desktop_icons, save_desktop_icons
from shell.settings.schema import settings_by_key


def test_create_save_load_remove_move(tmp_path: Path) -> None:
    path = tmp_path / "desktop-icons.json"
    bus = EventBus(dispatch_on_main=False)
    service = DesktopIconsService(bus, path=path)
    service.start()
    assert service.shortcuts == ()

    created = service.create(
        name="Firefox",
        type="application",
        target="firefox",
        icon="firefox",
        x=10,
        y=20,
    )
    assert created.id
    assert path.is_file()
    loaded = load_desktop_icons(path)
    assert len(loaded) == 1
    assert loaded[0].name == "Firefox"
    assert loaded[0].x == 10 and loaded[0].y == 20

    moved = service.move(created.id, 100, 200)
    assert moved is not None
    assert moved.x == 100 and moved.y == 200
    reloaded = load_desktop_icons(path)
    assert reloaded[0].x == 100 and reloaded[0].y == 200

    assert service.remove(created.id) is True
    assert service.shortcuts == ()
    assert load_desktop_icons(path) == ()


def test_corrupt_json_keeps_file_and_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "desktop-icons.json"
    path.write_text("{not-json", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert load_desktop_icons(path) == ()
    assert path.read_text(encoding="utf-8") == before


def test_invalid_payload_keeps_file(tmp_path: Path) -> None:
    path = tmp_path / "desktop-icons.json"
    path.write_text(json.dumps({"version": 1, "icons": "nope"}), encoding="utf-8")
    before = path.read_bytes()
    assert load_desktop_icons(path) == ()
    assert path.read_bytes() == before


def test_persist_roundtrip_dict(tmp_path: Path) -> None:
    path = tmp_path / "desktop-icons.json"
    shortcut = DesktopShortcut(
        id=new_shortcut_id(),
        name="Docs",
        type="directory",
        target="/home/user/Documents",
        icon="folder",
        x=5,
        y=6,
    )
    save_desktop_icons(path, (shortcut,))
    loaded = load_desktop_icons(path)
    assert len(loaded) == 1
    assert loaded[0].to_dict()["target"] == "/home/user/Documents"


def test_open_application_uses_launcher() -> None:
    launched: list[str] = []
    shortcut = DesktopShortcut(
        id="1",
        name="Term",
        type="application",
        target="xfce4-terminal",
        icon="",
        x=0,
        y=0,
    )
    open_shortcut(shortcut, launch_application=launched.append)
    assert launched == ["xfce4-terminal"]


def test_open_file_and_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hi", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    commands: list[list[str]] = []

    def executor(command):
        commands.append(list(command))

    open_shortcut(
        DesktopShortcut(
            id="f",
            name="Notes",
            type="file",
            target=str(file_path),
            x=0,
            y=0,
        ),
        executor=executor,
    )
    open_shortcut(
        DesktopShortcut(
            id="d",
            name="Folder",
            type="directory",
            target=str(folder),
            x=0,
            y=0,
        ),
        executor=executor,
    )
    assert len(commands) == 2
    assert commands[0][0] == "xdg-open"
    assert commands[1][0] == "xdg-open"


def test_command_type_blocked() -> None:
    shortcut = DesktopShortcut(
        id="c",
        name="Bad",
        type="command",
        target="rm -rf /",
        x=0,
        y=0,
    )
    try:
        open_shortcut(shortcut)
    except DesktopOpenError as error:
        assert "disabled" in str(error)
        return
    raise AssertionError("expected DesktopOpenError")


def test_create_rejects_command_type(tmp_path: Path) -> None:
    service = DesktopIconsService(EventBus(dispatch_on_main=False), path=tmp_path / "d.json")
    service.start()
    try:
        service.create(name="x", type="command", target="echo hi")  # type: ignore[arg-type]
    except ValueError as error:
        assert "cannot be created" in str(error)
        return
    raise AssertionError("expected ValueError")


def test_create_rejects_relative_file_path(tmp_path: Path) -> None:
    service = DesktopIconsService(EventBus(dispatch_on_main=False), path=tmp_path / "d.json")
    service.start()
    try:
        service.create(name="x", type="file", target="relative.txt")
    except ValueError as error:
        assert "absolute" in str(error)
        return
    raise AssertionError("expected ValueError")


def test_open_action_allowlist() -> None:
    called: list[str] = []

    def dispatch(name: str) -> str | None:
        called.append(name)
        return None

    open_shortcut(
        DesktopShortcut(
            id="a",
            name="Launcher",
            type="action",
            target="launcher",
            x=0,
            y=0,
        ),
        dispatch_jugoo_action=dispatch,
    )
    assert called == ["launcher"]
    try:
        open_shortcut(
            DesktopShortcut(
                id="b",
                name="Nope",
                type="action",
                target="not-a-real-action",
                x=0,
                y=0,
            ),
            dispatch_jugoo_action=dispatch,
        )
    except DesktopOpenError:
        return
    raise AssertionError("expected DesktopOpenError")


def test_settings_catalog_has_desktop_icons_keys() -> None:
    keys = settings_by_key()
    assert "escritorio.icons_enabled" in keys
    assert "escritorio.icons_editor" in keys


def main() -> None:
    with __import__("tempfile").TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_create_save_load_remove_move(tmp)
        test_corrupt_json_keeps_file_and_returns_empty(tmp)
        test_invalid_payload_keeps_file(tmp)
        test_persist_roundtrip_dict(tmp)
        test_open_application_uses_launcher()
        test_open_file_and_directory(tmp)
        test_command_type_blocked()
        test_create_rejects_command_type(tmp)
        test_create_rejects_relative_file_path(tmp)
        test_open_action_allowlist()
        test_settings_catalog_has_desktop_icons_keys()
    print("desktop icons tests OK")


if __name__ == "__main__":
    main()
