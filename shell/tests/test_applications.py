from __future__ import annotations

import json
from pathlib import Path

from shell.models import (
    DesktopApplication,
    Window,
    filter_applications,
    new_instance_command,
    next_window_to_focus,
    normalize_desktop_id,
    pin_application,
    split_pinned_dock,
    unpin_application,
    window_matches_application,
    windows_for_application,
)
from shell.eventbus import EventBus
from shell.servicios.aplicaciones.applications import (
    APPLICATIONS_CHANGED,
    ApplicationsService,
)
from shell.servicios.aplicaciones.desktop import read_desktop_application, strip_exec_field_codes
from shell.servicios.aplicaciones.store import (
    load_application_prefs,
    load_pinned_ids,
    save_application_prefs,
    save_pinned_ids,
)


def _app(app_id: str, name: str, *, wm_class: str = "") -> DesktopApplication:
    return DesktopApplication(id=app_id, name=name, icon="app", wm_class=wm_class)


def test_normalize_desktop_id_strips_suffix() -> None:
    assert normalize_desktop_id("firefox.desktop") == "firefox"
    assert normalize_desktop_id("  org.kde.dolphin  ") == "org.kde.dolphin"


def test_pin_appends_and_ignores_duplicates() -> None:
    pinned = pin_application((), "kitty")
    pinned = pin_application(pinned, "firefox.desktop")
    pinned = pin_application(pinned, "kitty")

    assert pinned == ("kitty", "firefox")


def test_unpin_closes_gap_and_preserves_order() -> None:
    pinned = ("zen-browser", "org.kde.dolphin", "kitty", "code")

    assert unpin_application(pinned, "org.kde.dolphin") == ("zen-browser", "kitty", "code")
    assert unpin_application(pinned, "missing") == pinned


def test_split_pinned_dock_keeps_nine_plus_overflow() -> None:
    ids = tuple(f"app-{index}" for index in range(1, 13))

    visible, overflow, has_expand = split_pinned_dock(ids, 9)

    assert visible == ids[:9]
    assert overflow == ids[9:]
    assert has_expand is True
    assert split_pinned_dock(ids[:9], 9) == (ids[:9], (), False)


def test_window_matching_uses_id_and_wm_class() -> None:
    dolphin = _app("org.kde.dolphin", "Dolphin", wm_class="dolphin")
    firefox = _app("firefox", "Firefox")
    window = Window("0x1", "org.kde.dolphin", "Home", 1)

    assert window_matches_application(window, dolphin)
    assert not window_matches_application(window, firefox)
    assert windows_for_application(dolphin, (window,)) == (window,)


def test_next_window_to_focus_cycles() -> None:
    first = Window("0x1", "kitty", "one", 1)
    second = Window("0x2", "kitty", "two", 1)

    assert next_window_to_focus((first, second), "").address == "0x1"
    assert next_window_to_focus((first, second), "0x1").address == "0x2"
    assert next_window_to_focus((first, second), "0x2").address == "0x1"
    assert next_window_to_focus((), "0x1") is None


def test_filter_applications_prefers_favorites_and_prefix() -> None:
    firefox = _app("firefox", "Firefox")
    files = _app("org.kde.dolphin", "Dolphin")
    code = _app("code", "Code - OSS")

    results = filter_applications((firefox, files, code), "do", ("org.kde.dolphin",))

    assert [item.id for item in results] == ["org.kde.dolphin"]

    unfiltered = filter_applications((firefox, files, code), "", ("code",))
    assert unfiltered[0].id == "code"
    assert [item.id for item in unfiltered] == ["code", "org.kde.dolphin", "firefox"]


def test_filter_applications_empty_query_returns_empty_state_friendly_list() -> None:
    assert filter_applications((), "firefox") == ()


def test_strip_exec_field_codes_removes_placeholders() -> None:
    assert strip_exec_field_codes("firefox %u") == "firefox"
    assert strip_exec_field_codes("code %F --new-window") == "code --new-window"


def test_read_desktop_application_skips_hidden(tmp_path: Path) -> None:
    visible = tmp_path / "kitty.desktop"
    visible.write_text(
        "[Desktop Entry]\nType=Application\nName=Kitty\nExec=kitty\nIcon=kitty\n",
        encoding="utf-8",
    )
    hidden = tmp_path / "secret.desktop"
    hidden.write_text(
        "[Desktop Entry]\nType=Application\nName=Secret\nExec=secret\nNoDisplay=true\n",
        encoding="utf-8",
    )

    parsed = read_desktop_application(visible)
    assert parsed is not None
    assert parsed.id == "kitty"
    assert parsed.name == "Kitty"
    assert read_desktop_application(hidden) is None


def test_pinned_store_roundtrip_preserves_order(tmp_path: Path) -> None:
    path = tmp_path / "pinned-apps.json"
    save_pinned_ids(path, ("zen-browser", "org.kde.dolphin", "kitty"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pinned"] == ["zen-browser", "org.kde.dolphin", "kitty"]
    assert payload["favorites"] == []
    assert load_pinned_ids(path) == ("zen-browser", "org.kde.dolphin", "kitty")


def test_application_prefs_keep_pins_and_favorites_independent(tmp_path: Path) -> None:
    path = tmp_path / "pinned-apps.json"
    save_application_prefs(path, ("firefox", "kitty"), ("org.kde.dolphin", "firefox"))

    pinned, favorites = load_application_prefs(path)
    assert pinned == ("firefox", "kitty")
    assert favorites == ("org.kde.dolphin", "firefox")

    save_pinned_ids(path, ("kitty",))
    pinned, favorites = load_application_prefs(path)
    assert pinned == ("kitty",)
    assert favorites == ("org.kde.dolphin", "firefox")


def test_application_prefs_load_v1_without_favorites(tmp_path: Path) -> None:
    path = tmp_path / "pinned-apps.json"
    path.write_text(
        json.dumps({"version": 1, "pinned": ["firefox", "kitty.desktop"]}) + "\n",
        encoding="utf-8",
    )
    pinned, favorites = load_application_prefs(path)
    assert pinned == ("firefox", "kitty")
    assert favorites == ()

    path.write_text(json.dumps(["zen-browser", "org.kde.dolphin"]) + "\n", encoding="utf-8")
    pinned, favorites = load_application_prefs(path)
    assert pinned == ("zen-browser", "org.kde.dolphin")
    assert favorites == ()


def test_new_instance_command_prefers_desktop_action() -> None:
    firefox = DesktopApplication(
        id="firefox",
        name="Firefox",
        icon="firefox",
        exec_cmd="firefox",
        new_instance_exec="firefox --new-window",
    )
    terminal = DesktopApplication(
        id="kitty",
        name="Kitty",
        icon="kitty",
        exec_cmd="kitty",
        terminal=True,
    )
    generic = DesktopApplication(id="foo", name="Foo", icon="foo", exec_cmd="foo --profile x")

    assert new_instance_command(firefox) == "firefox --new-window"
    assert new_instance_command(terminal) == "kitty"
    assert new_instance_command(generic) == "foo --profile x --new-window"


def test_read_desktop_application_new_window_action(tmp_path: Path) -> None:
    desktop = tmp_path / "firefox.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Firefox\n"
        "Exec=firefox %u\n"
        "Icon=firefox\n"
        "Actions=new-window;\n"
        "\n"
        "[Desktop Action new-window]\n"
        "Name=New Window\n"
        "Exec=firefox --new-window\n",
        encoding="utf-8",
    )
    parsed = read_desktop_application(desktop)
    assert parsed is not None
    assert parsed.exec_cmd == "firefox"
    assert parsed.new_instance_exec == "firefox --new-window"


def test_applications_service_pins_emits_and_launches(tmp_path: Path) -> None:
    desktop_dir = tmp_path / "applications"
    desktop_dir.mkdir()
    (desktop_dir / "kitty.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Kitty\nExec=kitty\nIcon=kitty\n",
        encoding="utf-8",
    )
    (desktop_dir / "firefox.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox %u\nIcon=firefox\n",
        encoding="utf-8",
    )
    launched: list[list[str]] = []
    events: list[object] = []
    bus = EventBus()
    bus.subscribe(APPLICATIONS_CHANGED, events.append)
    service = ApplicationsService(
        bus,
        path=tmp_path / "pinned-apps.json",
        directories=(desktop_dir,),
        executor=lambda command: launched.append(list(command)),
    )
    service.start()
    assert {app.id for app in service.snapshot.applications} == {"kitty", "firefox"}

    service.pin("firefox")
    service.pin("kitty")
    service.unpin("firefox")
    assert service.snapshot.pinned_ids == ("kitty",)
    assert load_pinned_ids(tmp_path / "pinned-apps.json") == ("kitty",)
    assert events

    service.launch("firefox")
    assert launched
    assert launched[0][-1] == "firefox" or launched[0][-1] == "firefox.desktop" or "firefox" in launched[0]

    launched.clear()
    service.favorite("firefox")
    service.pin("firefox")
    assert service.snapshot.pinned_ids == ("kitty", "firefox")
    assert service.snapshot.favorite_ids == ("firefox",)
    service.unfavorite("firefox")
    assert service.snapshot.pinned_ids == ("kitty", "firefox")
    assert service.snapshot.favorite_ids == ()
    service.unpin("firefox")
    service.favorite("firefox")
    assert service.snapshot.pinned_ids == ("kitty",)
    assert service.snapshot.favorite_ids == ("firefox",)
    pinned, favorites = load_application_prefs(tmp_path / "pinned-apps.json")
    assert pinned == ("kitty",)
    assert favorites == ("firefox",)

    launched.clear()
    service.launch_new_instance("firefox")
    assert launched
    assert any("--new-window" in part for part in launched[0])

    service.close()


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

