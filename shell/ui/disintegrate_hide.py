"""Hide Jugoo popups with the Hyprland disintegration overlay on the card only."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, Gtk, GtkLayerShell

_DISINTEGRATE_SCRIPT = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "hypr"
    / "scripts"
    / "close_disintegrate.py"
)

# Interactive Jugoo popups that are NOT door-layers (puertas keep door close).
_NAME_HINTS = (
    "settings",
    "control",
    "network-panel",
    "audio-panel",
    "media-panel",
    "tasks",
    "session",
    "power",
)

# Fullscreen door pickers — never steal their puerta close animation.
_DOOR_LAYER_NAMES = (
    "shell-app-launcher",
    "shell-clipboard-picker",
    "shell-emoji-picker",
)

_CARD_STYLE_CLASSES = (
    "launcher-card-host",
    "launcher-card",
    "settings-card",
    "control-center-popup-content",
    "tasks-popup-content",
)


def _eligible(window: Gtk.Window) -> bool:
    name = (window.get_name() or "").lower()
    title = window.get_title() or ""
    if name in _DOOR_LAYER_NAMES or any(
        name.startswith(prefix) for prefix in _DOOR_LAYER_NAMES
    ):
        return False
    if title.startswith("Jugoo "):
        # Door pickers also use Jugoo titles; skip those layers.
        if GtkLayerShell.is_layer_window(window):
            namespace = (GtkLayerShell.get_namespace(window) or "").lower()
            if namespace in _DOOR_LAYER_NAMES or "picker" in namespace or "launcher" in namespace:
                return False
        return True
    if not name.startswith("shell-"):
        return False
    return any(hint in name for hint in _NAME_HINTS)


def _hypr_json(command: str):
    try:
        result = subprocess.run(
            ["hyprctl", "-j", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _layer_global_origin(window: Gtk.Window) -> tuple[int, int] | None:
    """Compositor-global top-left of a GtkLayerShell surface via hyprctl layers."""
    if not GtkLayerShell.is_layer_window(window):
        return None
    namespace = GtkLayerShell.get_namespace(window) or window.get_name() or ""
    if not namespace:
        return None
    payload = _hypr_json("layers")
    if not isinstance(payload, dict):
        return None
    for _monitor_name, monitor_data in payload.items():
        if not isinstance(monitor_data, dict):
            continue
        levels = monitor_data.get("levels") or {}
        if not isinstance(levels, dict):
            continue
        for _level, surfaces in levels.items():
            if not isinstance(surfaces, list):
                continue
            for surface in surfaces:
                if not isinstance(surface, dict):
                    continue
                if surface.get("namespace") != namespace:
                    continue
                try:
                    return int(surface["x"]), int(surface["y"])
                except (KeyError, TypeError, ValueError):
                    continue
    return None


def _client_global_rect(window: Gtk.Window) -> tuple[int, int, int, int] | None:
    """Compositor-global rect for an xdg toplevel (floating Jugoo popups)."""
    title = window.get_title() or ""
    address = None
    # Prefer title match; floating Jugoo popups have stable titles.
    clients = _hypr_json("clients")
    if not isinstance(clients, list):
        return None
    for client in clients:
        if not isinstance(client, dict):
            continue
        if title and client.get("title") == title:
            try:
                x, y = client["at"]
                w, h = client["size"]
                return int(x), int(y), int(w), int(h)
            except (KeyError, TypeError, ValueError, IndexError):
                continue
    return None


def _walk_find_style(widget: Gtk.Widget, class_names: tuple[str, ...]) -> Gtk.Widget | None:
    try:
        classes = set(widget.get_style_context().list_classes())
    except Exception:
        classes = set()
    if classes.intersection(class_names):
        return widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found = _walk_find_style(child, class_names)
            if found is not None:
                return found
    return None


def _content_widget(window: Gtk.Window) -> Gtk.Widget:
    """The visible card/clip — never the fullscreen backdrop layer."""
    door = getattr(window, "_door", None)
    if isinstance(door, Gtk.Widget) and door.get_mapped():
        return door
    card_host = getattr(window, "_card_host", None)
    if isinstance(card_host, Gtk.Widget) and card_host.get_mapped():
        return card_host
    found = _walk_find_style(window, _CARD_STYLE_CLASSES)
    if found is not None:
        return found
    return window


def _widget_size(widget: Gtk.Widget) -> tuple[int, int]:
    allocation = widget.get_allocation()
    width = int(allocation.width)
    height = int(allocation.height)
    if width <= 1 or height <= 1:
        _min_w, nat_w = widget.get_preferred_width()
        _min_h, nat_h = widget.get_preferred_height()
        width = max(width, int(nat_w))
        height = max(height, int(nat_h))
    return max(width, 1), max(height, 1)


def _content_geometry(window: Gtk.Window) -> str | None:
    """grim geometry for the card only, in compositor-global coordinates."""
    content = _content_widget(window)
    width, height = _widget_size(content)
    if width < 8 or height < 8:
        return None

    # Relative offset of the card inside the toplevel.
    rel = content.translate_coordinates(window, 0, 0)
    if rel is None:
        allocation = content.get_allocation()
        rel_x, rel_y = int(allocation.x), int(allocation.y)
    else:
        rel_x, rel_y = int(rel[0]), int(rel[1])

    layer_origin = _layer_global_origin(window)
    if layer_origin is not None:
        base_x, base_y = layer_origin
        # Fullscreen layer + no distinct card → refuse (would flash the whole screen).
        if content is window:
            return None
        return f"{base_x + rel_x},{base_y + rel_y} {width}x{height}"

    client = _client_global_rect(window)
    if client is not None:
        cx, cy, cw, ch = client
        if content is window:
            return f"{cx},{cy} {cw}x{ch}"
        return f"{cx + rel_x},{cy + rel_y} {width}x{height}"

    # Last resort: GDK origin (often wrong on Wayland after moves).
    gdk_window = window.get_window()
    if gdk_window is None:
        return None
    origin = gdk_window.get_origin()
    if len(origin) == 3:
        _ok, ox, oy = origin
    else:
        ox, oy = origin
    if content is window and (width > 1200 or height > 800):
        # Likely a mis-measured fullscreen surface — do not disintegrate the monitor.
        return None
    return f"{int(ox) + rel_x},{int(oy) + rel_y} {width}x{height}"


def disintegrate_hide(window: Gtk.Window) -> bool:
    """Capture the card, start the overlay, then hide instantly.

    Returns True when handled (skip fade/door). False → normal Jugoo hide.
    """
    if not _eligible(window) or not window.get_visible():
        return False
    if not _DISINTEGRATE_SCRIPT.is_file():
        return False

    geometry = _content_geometry(window)
    if geometry is None:
        return False

    try:
        subprocess.run(
            [
                "python3",
                str(_DISINTEGRATE_SCRIPT),
                "--overlay-only",
                "--geometry",
                geometry,
            ],
            check=True,
            timeout=2.5,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    window.hide()
    try:
        window.set_opacity(1.0)
    except Exception:
        pass
    return True
