from __future__ import annotations

import re
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from shell.identity import project_root
from shell.ui.theme import (
    color_to_rgb,
    compile_gtk_css,
    discover_themes,
    export_hypr_theme,
    load_theme,
)


ROOT = project_root()
SPACE_PATH = ROOT / "themes" / "space.toml"
STYLE_PATH = ROOT / "shell" / "style.css"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\s*\(")


def test_space_theme_has_complete_semantic_contract() -> None:
    theme = load_theme(SPACE_PATH)
    assert theme.key == "space"
    assert theme.name == "Space"
    assert theme.colors.background == "#070A14"
    assert theme.colors.primary == "#7C8CFF"
    assert theme.colors.accent == "#D946EF"
    assert theme.effects.blur == 8
    assert theme.shape.radius == 12
    assert theme.animation.enabled


def test_theme_catalog_discovers_space() -> None:
    assert discover_themes(ROOT / "themes")["space"] == SPACE_PATH


def test_invalid_theme_is_rejected_before_application() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "broken.toml"
        path.write_text("[colors]\nbackground = 'red'\n", encoding="utf-8")
        try:
            load_theme(path)
        except ValueError as error:
            assert "missing [meta] section" in str(error) or "colors." in str(error)
        else:
            raise AssertionError("invalid theme was accepted")


def test_structural_css_contains_no_palette_literals() -> None:
    structural_css = STYLE_PATH.read_text(encoding="utf-8")
    assert COLOR_LITERAL.search(structural_css) is None


def test_compiled_space_css_parses_in_gtk3() -> None:
    theme = load_theme(SPACE_PATH)
    css = compile_gtk_css(theme, STYLE_PATH.read_text(encoding="utf-8"))
    assert "@define-color theme_background #070A14;" in css
    assert "@define-color shell_accent #7C8CFF;" in css
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode("utf-8"))


def test_theme_radius_scales_structural_radii() -> None:
    theme = load_theme(SPACE_PATH)
    css = compile_gtk_css(theme, "box { border-radius: 8px 0 16px 0; }")
    assert "border-radius: 8px 0 16px 0" in css


def test_theme_color_converts_for_cairo() -> None:
    assert color_to_rgb("#FF8000") == (1.0, 128 / 255.0, 0.0)


def test_hyprland_export_uses_semantic_theme() -> None:
    theme = load_theme(SPACE_PATH)
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "jugoo_theme_generated.lua"
        export_hypr_theme(theme, destination)
        payload = destination.read_text(encoding="utf-8")
    assert 'active_border = { colors = { "rgba(7c8cffff)", "rgba(d946efff)" }' in payload
    assert "rounding = 12" in payload
    assert "size = 8" in payload


if __name__ == "__main__":
    test_space_theme_has_complete_semantic_contract()
    test_theme_catalog_discovers_space()
    test_invalid_theme_is_rejected_before_application()
    test_structural_css_contains_no_palette_literals()
    test_compiled_space_css_parses_in_gtk3()
    test_theme_radius_scales_structural_radii()
    test_theme_color_converts_for_cairo()
    test_hyprland_export_uses_semantic_theme()
