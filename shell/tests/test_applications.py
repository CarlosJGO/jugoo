from __future__ import annotations

import json
from pathlib import Path

from shell.models import (
    DesktopApplication,
    Window,
    filter_applications,
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
from shell.servicios.aplicaciones.store import load_pinned_ids, save_pinned_ids


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


def test_filter_applications_prefers_pinned_and_prefix() -> None:
    firefox = _app("firefox", "Firefox")
    files = _app("org.kde.dolphin", "Dolphin")
    code = _app("code", "Code - OSS")

    results = filter_applications((firefox, files, code), "do", ("org.kde.dolphin",))

    assert [item.id for item in results] == ["org.kde.dolphin"]

    unfiltered = filter_applications((firefox, files, code), "", ("code",))
    assert unfiltered[0].id == "code"


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
    assert load_pinned_ids(path) == ("zen-browser", "org.kde.dolphin", "kitty")


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

    service.close()
