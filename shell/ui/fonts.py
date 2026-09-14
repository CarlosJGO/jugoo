"""System UI font discovery for Apariencia settings."""

from __future__ import annotations

from functools import lru_cache

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import PangoCairo

# ComboBoxText rejects empty ids; map this sentinel to the stored "".
SYSTEM_UI_FONT_CHOICE = "__system__"


def normalize_ui_font(value: object) -> str:
    """Return a real family name, or empty string for the system default."""
    text = str(value or "").strip()
    if text in ("", SYSTEM_UI_FONT_CHOICE):
        return ""
    return text


@lru_cache(maxsize=1)
def list_system_font_families() -> tuple[str, ...]:
    """Return installed font family names (sorted, unique)."""
    font_map = PangoCairo.font_map_get_default()
    if font_map is None:
        return ()
    names: set[str] = set()
    for family in font_map.list_families():
        name = (family.get_name() or "").strip()
        if name:
            names.add(name)
    return tuple(sorted(names, key=str.casefold))


def ui_font_choices() -> tuple[tuple[str, str], ...]:
    """Choices for ``apariencia.ui_font`` (``__system__`` = system default)."""
    choices: list[tuple[str, str]] = [
        (SYSTEM_UI_FONT_CHOICE, "Sistema (predeterminada)")
    ]
    for name in list_system_font_families():
        choices.append((name, name))
    return tuple(choices)


def css_font_family(family: str) -> str:
    """Quote a font family for GTK CSS ``font-family``."""
    cleaned = family.strip().replace("\\", "").replace('"', "")
    if not cleaned:
        return "sans-serif"
    return f'"{cleaned}"'
