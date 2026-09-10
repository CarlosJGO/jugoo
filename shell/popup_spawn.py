"""Publish floating-popup spawn coordinates for Hyprland to apply at map.

GTK cannot place an xdg_toplevel before map. Hyprland's static ``move`` window
rule can, if the coordinates are known first. Callers write those coordinates
here *before* ``present_popup``; Notifications never call this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .window_identity import (
    MonitorRect,
    anchor_button_geometry,
    compute_popup_top_left,
    popup_window_size,
)


SPAWN_STATE_NAME = "popup-spawn.json"


@dataclass(frozen=True)
class SpawnMonitor:
    name: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class SpawnPlacement:
    title: str
    x: int
    y: int
    local_x: int
    local_y: int
    monitor: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "x": self.x,
            "y": self.y,
            "local_x": self.local_x,
            "local_y": self.local_y,
            "monitor": self.monitor,
        }


def spawn_state_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return Path(runtime) / "jugoo" / SPAWN_STATE_NAME


def monitors_from_hyprctl_payload(payload: object) -> tuple[SpawnMonitor, ...]:
    if not isinstance(payload, list):
        return ()
    monitors: list[SpawnMonitor] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        try:
            monitors.append(
                SpawnMonitor(
                    name=str(entry.get("name") or ""),
                    x=int(entry["x"]),
                    y=int(entry["y"]),
                    width=int(entry["width"]),
                    height=int(entry["height"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(monitors)


def monitor_containing(x: int, y: int, monitors: tuple[SpawnMonitor, ...]) -> SpawnMonitor | None:
    for monitor in monitors:
        if monitor.x <= x < monitor.x + monitor.width and monitor.y <= y < monitor.y + monitor.height:
            return monitor
    return monitors[0] if monitors else None


def placement_for_anchor(
    *,
    title: str,
    button_center_x: int,
    button_bottom: int,
    popup_width: int,
    popup_height: int,
    offset: int,
    monitors: tuple[SpawnMonitor, ...],
    fixed_top: int | None = None,
    margin: int | None = None,
    extra_y_offset: int = 0,
) -> SpawnPlacement | None:
    """Top-left spawn coordinates, global and monitor-local."""
    monitor = monitor_containing(button_center_x, button_bottom, monitors)
    rect = None
    if monitor is not None:
        rect = MonitorRect(
            x=monitor.x,
            y=monitor.y,
            width=monitor.width,
            height=monitor.height,
        )
    left, top = compute_popup_top_left(
        button_center_x=button_center_x,
        button_bottom=button_bottom,
        popup_width=popup_width,
        popup_height=popup_height,
        offset=offset + extra_y_offset,
        fixed_top=(fixed_top + extra_y_offset) if fixed_top is not None else None,
        monitor=rect,
        margin=margin,
    )
    if monitor is None:
        return SpawnPlacement(
            title=title,
            x=left,
            y=top,
            local_x=left,
            local_y=top,
            monitor="",
        )
    return SpawnPlacement(
        title=title,
        x=left,
        y=top,
        local_x=left - monitor.x,
        local_y=top - monitor.y,
        monitor=monitor.name,
    )


def read_spawn_payload(path: Path | None = None) -> dict[str, object]:
    dest = path if path is not None else spawn_state_path()
    if not dest.is_file():
        return {}
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def write_spawn_payload(placement: SpawnPlacement, *, path: Path | None = None) -> Path:
    dest = path if path is not None else spawn_state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = read_spawn_payload(dest)
    existing[placement.title] = placement.to_dict()
    encoded = json.dumps(existing, indent=2, sort_keys=True) + "\n"
    handle, tmp_name = tempfile.mkstemp(
        prefix="popup-spawn.",
        suffix=".json",
        dir=str(dest.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(encoded)
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dest


def notify_hyprland_spawn_reload() -> None:
    """Ask Hyprland to install named ``move`` rules from the spawn file now."""
    try:
        subprocess.run(
            ["hyprctl", "eval", "jugoo_reload_popup_spawns()"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def query_spawn_monitors() -> tuple[SpawnMonitor, ...]:
    try:
        result = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0 or not result.stdout.strip():
        return ()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ()
    return monitors_from_hyprctl_payload(payload)


def publish_popup_spawn(
    window: Gtk.Window,
    anchor: Gtk.Widget,
    *,
    title: str,
    offset: int,
    fixed_top: int | None = None,
    margin: int | None = None,
    extra_y_offset: int = 0,
    notify: bool = True,
) -> int | None:
    """Compute spawn coordinates, publish them, and return the top edge used."""
    geometry = anchor_button_geometry(anchor)
    if geometry is None:
        return None

    popup_width, popup_height = popup_window_size(window)
    placement = placement_for_anchor(
        title=title,
        button_center_x=geometry.center_x,
        button_bottom=geometry.bottom,
        popup_width=popup_width,
        popup_height=popup_height,
        offset=offset,
        monitors=query_spawn_monitors(),
        fixed_top=fixed_top,
        margin=margin,
        extra_y_offset=extra_y_offset,
    )
    if placement is None:
        return None
    write_spawn_payload(placement)
    if notify:
        notify_hyprland_spawn_reload()
    return placement.y
