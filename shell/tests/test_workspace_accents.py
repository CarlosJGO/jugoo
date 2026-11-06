"""Tests for per-workspace accent colors and special detection."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from shell.eventbus import EventBus
from shell.models import WorkspaceRecord, compose_workspaces
from shell.servicios.escritorio.hyprland import HyprlandService
from shell.ui.workspace_accents import (
    DEFAULT_ACCENT_COLORS,
    accent_class_for_workspace,
    accent_key_for_workspace,
    build_accent_css,
    configured_workspace_names,
    discover_accent_keys,
    is_hypr_special_name,
    parse_accent_colors,
    resolve_accent_color,
    serialize_accent_colors,
)
from shell.widgets.barra.workspace import workspace_presentation


def test_hypr_special_name_detection() -> None:
    assert is_hypr_special_name("special:special")
    assert is_hypr_special_name("special:minimizados")
    assert is_hypr_special_name("special")
    assert not is_hypr_special_name("gaming")
    assert not is_hypr_special_name("1")
    assert not is_hypr_special_name("3")


def test_accent_keys_are_per_workspace() -> None:
    assert accent_key_for_workspace("special:special") == "special"
    assert accent_key_for_workspace("special:minimizados") == "minimizados"
    assert accent_key_for_workspace("gaming") == "gaming"
    assert accent_key_for_workspace("1") is None
    assert accent_class_for_workspace("special:minimizados") == "ws-accent-minimizados"
    assert accent_class_for_workspace("gaming") == "ws-accent-gaming"


def test_named_workspace_labels_use_semantic_icons() -> None:
    assert workspace_presentation("2") == (
        "Workspace 2",
        "view-paged-symbolic",
    )
    assert workspace_presentation("gaming") == (
        "Juegos",
        "applications-games-symbolic",
    )
    assert workspace_presentation("special:minimizados") == (
        "Minimizados",
        "window-minimize-symbolic",
    )
    assert workspace_presentation("special:scratch") == (
        "Scratch",
        "view-restore-symbolic",
    )


def test_hyprland_special_workspace_without_numeric_id_is_stable() -> None:
    raw_workspace = {
        "address": "special:scratch",
        "type": "special",
        "name": "special:scratch",
    }

    workspace = HyprlandService._workspace(raw_workspace)
    window = HyprlandService._window(
        {
            "address": "0x1",
            "class": "zen",
            "title": "Browser",
            "workspace": raw_workspace,
        }
    )

    assert workspace.id == window.workspace_id
    assert -(2**31) <= workspace.id < 0
    assert workspace.is_special
    assert HyprlandService._workspace(raw_workspace).id == workspace.id


def test_resolve_colors_differ_per_key() -> None:
    overrides = {
        "special": "#D946EF",
        "minimizados": "#F5C76B",
        "gaming": "#5CE6A8",
    }
    assert resolve_accent_color("special", overrides) != resolve_accent_color(
        "minimizados", overrides
    )
    assert resolve_accent_color("gaming", overrides) != resolve_accent_color(
        "special", overrides
    )
    css = build_accent_css(overrides)
    assert "ws-accent-special" in css
    assert "ws-accent-minimizados" in css
    assert "ws-accent-gaming" in css
    assert "#5CE6A8" in css


def test_accent_css_includes_live_special_workspace_names() -> None:
    css = build_accent_css(
        DEFAULT_ACCENT_COLORS,
        workspace_names=("special:scratch",),
    )

    assert "button.workspace-button.named-workspace.ws-accent-scratch" in css
    assert resolve_accent_color("scratch", DEFAULT_ACCENT_COLORS) in css
    assert "border: 1px solid" not in css


def test_parse_and_serialize_roundtrip() -> None:
    raw = serialize_accent_colors({"gaming": "#abcdef", "bad": "nope"})
    parsed = parse_accent_colors(raw)
    assert parsed == {"gaming": "#ABCDEF"}


def test_discover_keys_from_hypr_file(tmp_path: Path) -> None:
    path = tmp_path / "workspaces.lua"
    path.write_text(
        'hl.workspace_rule({ workspace = "name:gaming", monitor = PRIMARY_MONITOR })\n'
        'hl.workspace_rule({ workspace = "special:minimizados", monitor = PRIMARY_MONITOR })\n'
        'hl.workspace_rule({ workspace = "1", monitor = MONITOR1 })\n',
        encoding="utf-8",
    )
    keys = discover_accent_keys(hypr_config_paths=(path,), live_names=("special:special",))
    assert "gaming" in keys
    assert "minimizados" in keys
    assert "special" in keys


def test_configured_workspace_names_include_named_and_special_rules(tmp_path: Path) -> None:
    path = tmp_path / "workspaces.lua"
    path.write_text(
        'hl.workspace_rule({ workspace = "name:gaming", default = true })\n'
        'hl.workspace_rule({ workspace = "special:minimizados" })\n'
        'hl.window_rule({ workspace = "special:scratch" })\n'
        'hl.dsp.focus({ workspace = "m+1" })\n'
        '-- hl.workspace_rule({ workspace = "name:disabled" })\n',
        encoding="utf-8",
    )

    assert configured_workspace_names((path,)) == (
        "gaming",
        "special:minimizados",
        "special:scratch",
    )


def test_compose_keeps_named_negative_id_workspace() -> None:
    records = (
        WorkspaceRecord(1, "1", False),
        WorkspaceRecord(-1337, "gaming", False),
        WorkspaceRecord(-99, "special:special", True),
        WorkspaceRecord(-98, "special:minimizados", True),
    )
    from shell.models import Window, compose_workspace_blocks

    windows = (
        Window("0x1", "kitty", "a", -1337),
        Window("0x2", "kitty", "b", -99),
        Window("0x3", "kitty", "c", -98),
    )
    composed = compose_workspaces(
        records,
        windows,
        active_workspace_id=1,
        persistent_workspaces=3,
        icon_for_window=lambda _w: "app",
        focused_window_address=None,
    )
    by_name = {workspace.name: workspace for workspace in composed}
    assert by_name["gaming"].is_special is False
    assert by_name["special:special"].is_special is True
    assert by_name["special:minimizados"].is_special is True
    assert accent_key_for_workspace(by_name["gaming"].name) == "gaming"
    assert accent_key_for_workspace(by_name["special:minimizados"].name) == "minimizados"

    # Named gaming must appear in the bar's extra strip, not only Hypr specials.
    block_ids = {
        workspace.id
        for block in compose_workspace_blocks(composed, 3)
        for workspace in block.workspaces
    }
    extra = [
        workspace
        for workspace in composed
        if workspace.is_special or workspace.id < 1
    ]
    assert by_name["gaming"].id not in block_ids
    assert {workspace.name for workspace in extra} == {
        "gaming",
        "special:special",
        "special:minimizados",
    }


def test_compose_keeps_empty_named_workspace_record() -> None:
    composed = compose_workspaces(
        (WorkspaceRecord(-1337, "gaming", False),),
        (),
        active_workspace_id=1,
        persistent_workspaces=None,
        icon_for_window=lambda _window: "app",
        focused_window_address=None,
    )

    assert len(composed) == 1
    assert composed[0].name == "gaming"
    assert composed[0].windows == ()


def test_compose_keeps_empty_special_workspace_record() -> None:
    composed = compose_workspaces(
        (WorkspaceRecord(-98, "special:minimizados", True),),
        (),
        active_workspace_id=1,
        persistent_workspaces=None,
        icon_for_window=lambda _window: "app",
        focused_window_address=None,
    )

    assert len(composed) == 1
    assert composed[0].name == "special:minimizados"
    assert composed[0].is_special
    assert composed[0].windows == ()


def test_full_snapshot_includes_configured_workspace_missing_from_hypr_json() -> None:
    service = HyprlandService(EventBus(), persistent_workspaces=None)
    service._configured_workspace_names = (
        "gaming",
        "special:minimizados",
        "special:scratch",
    )
    payloads = {
        "clients": [],
        "workspaces": [],
        "activeworkspace": {"id": 1},
        "activewindow": {},
    }

    with mock.patch.object(service, "_json", side_effect=lambda command: payloads[command]):
        snapshot = service._refresh_full()

    assert [
        (workspace.name, workspace.is_special, workspace.windows)
        for workspace in snapshot.workspaces
    ] == [
        ("gaming", False, ()),
        ("special:minimizados", True, ()),
        ("special:scratch", True, ()),
    ]


if __name__ == "__main__":
    test_hypr_special_name_detection()
    test_accent_keys_are_per_workspace()
    test_resolve_colors_differ_per_key()
    test_parse_and_serialize_roundtrip()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        test_discover_keys_from_hypr_file(Path(tmp))
    test_compose_keeps_named_negative_id_workspace()
    print("workspace accents OK")
