"""Per-workspace accent colors for Hypr specials and named workspaces."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Mapping

# Stable keys → default hex. Users override via widgets.workspace_accent_colors_json.
DEFAULT_ACCENT_COLORS: dict[str, str] = {
    "special": "#D946EF",
    "minimizados": "#F5C76B",
    "gaming": "#5CE6A8",
}

_FALLBACK_PALETTE: tuple[str, ...] = (
    "#7C8CFF",
    "#65B7FF",
    "#FF6B8A",
    "#8B5CF6",
    "#F5C76B",
    "#5CE6A8",
    "#D946EF",
    "#E8E9F2",
)

_HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
_WORKSPACE_RULE_RE = re.compile(
    r'workspace\s*=\s*"(?:special:|name:)?([^"]+)"',
    re.IGNORECASE,
)

_current_colors: dict[str, str] = dict(DEFAULT_ACCENT_COLORS)
_css_provider = None  # Gtk.CssProvider | None — lazy to avoid import cost at module load


def is_hypr_special_name(name: str) -> bool:
    """True only for Hyprland special/scratchpad workspaces (not hashed named ids)."""
    text = str(name or "").strip()
    return text == "special" or text.startswith("special:")


def accent_key_for_workspace(name: str) -> str | None:
    """Return the stable color key for a workspace name, or None for numeric ones."""
    text = str(name or "").strip()
    if not text:
        return None
    if text.startswith("special:"):
        suffix = text.split(":", 1)[1].strip()
        return suffix or "special"
    if text == "special":
        return "special"
    if text.isdigit():
        return None
    return text


def css_safe_key(key: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(key).strip()).strip("-").lower()
    return cleaned or "accent"


def normalize_hex(value: str) -> str | None:
    text = str(value or "").strip()
    if not _HEX_RE.match(text):
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    return text.upper()


def parse_accent_colors(raw: str | None) -> dict[str, str]:
    """Parse user JSON overrides; invalid entries are skipped."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        ident = str(key).strip()
        if not ident:
            continue
        color = normalize_hex(str(value))
        if color is None:
            continue
        result[ident] = color
    return result


def serialize_accent_colors(colors: Mapping[str, str]) -> str:
    cleaned = {
        str(key): color
        for key, value in colors.items()
        if (color := normalize_hex(str(value))) is not None and str(key).strip()
    }
    return json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True)


def default_accent_colors_json() -> str:
    return serialize_accent_colors(DEFAULT_ACCENT_COLORS)


def resolve_accent_color(key: str, overrides: Mapping[str, str] | None = None) -> str:
    table = overrides if overrides is not None else _current_colors
    direct = normalize_hex(str(table.get(key, "")))
    if direct is not None:
        return direct
    builtin = DEFAULT_ACCENT_COLORS.get(key)
    if builtin is not None:
        return builtin
    digest = sum(ord(char) for char in key) if key else 0
    return _FALLBACK_PALETTE[digest % len(_FALLBACK_PALETTE)]


def merged_accent_colors(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Defaults + overrides for every known key (overrides win)."""
    keys = set(DEFAULT_ACCENT_COLORS)
    if overrides:
        keys.update(overrides)
    keys.update(_current_colors)
    return {key: resolve_accent_color(key, overrides or _current_colors) for key in sorted(keys)}


def set_current_accent_colors(raw: str | None) -> dict[str, str]:
    """Update the process-wide color table used by the workspace strip."""
    global _current_colors
    overrides = parse_accent_colors(raw)
    _current_colors = {
        **DEFAULT_ACCENT_COLORS,
        **overrides,
    }
    _refresh_css_provider()
    return dict(_current_colors)


def current_accent_colors() -> dict[str, str]:
    return dict(_current_colors)


def accent_class_for_workspace(name: str) -> str | None:
    key = accent_key_for_workspace(name)
    if key is None:
        return None
    return f"ws-accent-{css_safe_key(key)}"


def display_label_for_key(key: str) -> str:
    labels = {
        "special": "Special (scratchpad)",
        "minimizados": "Minimizados",
        "gaming": "Gaming",
    }
    return labels.get(key, key)


def discover_accent_keys(
    *,
    hypr_config_paths: Iterable[Path] | None = None,
    live_names: Iterable[str] = (),
) -> tuple[str, ...]:
    """Keys for creatable specials/named workspaces from Hypr rules + live names."""
    keys: set[str] = set(DEFAULT_ACCENT_COLORS)
    for name in live_names:
        key = accent_key_for_workspace(name)
        if key is not None:
            keys.add(key)
    paths = tuple(hypr_config_paths) if hypr_config_paths is not None else default_hypr_workspace_paths()
    for path in paths:
        keys.update(_keys_from_hypr_file(path))
    return tuple(sorted(keys, key=lambda item: (display_label_for_key(item).casefold(), item)))


def default_hypr_workspace_paths() -> tuple[Path, ...]:
    root = Path.home() / ".config" / "hypr"
    return (
        root / "config" / "workspaces.lua",
        root / "workspaces.conf",
        root / "hyprland.conf",
    )


def _keys_from_hypr_file(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    found: set[str] = set()
    for match in _WORKSPACE_RULE_RE.finditer(text):
        raw = match.group(1).strip()
        if not raw or raw.isdigit():
            continue
        # Rules look like special:minimizados or gaming (from name:gaming).
        key = accent_key_for_workspace(raw if ":" in raw or raw == "special" else raw)
        if key is None and not raw.isdigit():
            key = raw
        if key is not None:
            found.add(key)
    # Also catch explicit special: in the file even if regex group already stripped prefix.
    for match in re.finditer(r"special:([A-Za-z0-9_-]+)", text):
        found.add(match.group(1))
    for match in re.finditer(r'name:([A-Za-z0-9_-]+)', text):
        found.add(match.group(1))
    return found


def build_accent_css(colors: Mapping[str, str] | None = None) -> str:
    table = colors if colors is not None else _current_colors
    chunks: list[str] = []
    for key, color in sorted(table.items()):
        hex_color = normalize_hex(str(color))
        if hex_color is None:
            continue
        safe = css_safe_key(key)
        chunks.append(
            f"""
button.workspace-button.ws-accent-{safe} {{
    background-color: alpha({hex_color}, 0.14);
    border: 1px solid alpha({hex_color}, 0.42);
    border-radius: 8px;
}}
button.workspace-button.ws-accent-{safe}:hover {{
    background-color: alpha({hex_color}, 0.20);
}}
button.workspace-button.ws-accent-{safe}.active {{
    background-color: alpha({hex_color}, 0.28);
}}
button.workspace-button.ws-accent-{safe}.active.audio-has-stream,
button.workspace-button.ws-accent-{safe}.active.audio-playing {{
    background-color: alpha({hex_color}, 0.28);
}}
""".strip()
        )
    return "\n".join(chunks)


def _refresh_css_provider() -> None:
    global _css_provider
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    css = build_accent_css(_current_colors)
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(css.encode("utf-8"))
    except Exception as error:  # noqa: BLE001 — keep shell alive on bad colors
        print(f"shell: workspace accent css rejected: {error}", flush=True)
        return
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    if _css_provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _css_provider)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )
    _css_provider = provider
