"""Stable GTK/Wayland window identity for shell toplevels."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .config import POPUP_EDGE_MARGIN
from .identity import (
    APPLICATION_ID,
    APPLICATION_NAME,
    TITLE_APP_LAUNCHER,
    TITLE_BAR,
    TITLE_CLOCK_CALENDAR,
    TITLE_CONTROL_CENTER,
    TITLE_MEDIA_POPUP,
    TITLE_MEMORY_POPUP,
    TITLE_NETWORK_PANEL,
    TITLE_NOTIFICATION_GROUP,
    TITLE_NOTIFICATION_TOAST,
    TITLE_NOTIFICATIONS,
    TITLE_PINNED_OVERFLOW,
    TITLE_POWER_CONFIRM,
    TITLE_POWER_MENU,
    TITLE_TASKS,
    TITLE_VOLUME_OSD,
    TITLE_WORKSPACE_AUDIO,
    TITLE_WORKSPACE_PANEL,
    WAYLAND_APP_ID,
)

PROGRAM_CLASS = WAYLAND_APP_ID
HYPR_WINDOW_CLASS = WAYLAND_APP_ID

# Interactive popups that keep keyboard focus on purpose:
# - Control Center / Network: WiFi password entry
# - Media popup: player combo + seek
# - Workspace audio / panel: volume sliders and device selectors
# - Notifications / Power: buttons and confirmation dialogs
# - App launcher: search entry and keyboard navigation
# - Tasks: title/notes entry when adding a task
# OSD windows must never take focus or keyboard (see configure_osd_window).


@dataclass(frozen=True)
class MonitorRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class AnchorButtonGeometry:
    left: int
    top: int
    width: int
    height: int
    center_x: int
    bottom: int


def init_window_identity() -> None:
    """Call once before creating any GTK windows.

    Wayland app_id comes from Gtk.Application's id when the window is attached,
    and from ``g_get_prgname()`` otherwise. ``python3 -m shell`` would otherwise
    expose class ``__main__.py``.
    """
    GLib.set_prgname(WAYLAND_APP_ID)
    GLib.set_application_name(APPLICATION_NAME)
    Gdk.set_program_class(WAYLAND_APP_ID)


def is_wayland_session() -> bool:
    return Gdk.Display.get_default().__class__.__name__ == "GdkWaylandDisplay"


def configure_toplevel(
    window: Gtk.Window,
    *,
    title: str,
    application: Gtk.Application | None = None,
) -> None:
    """Set Hyprland-visible metadata for a shell toplevel."""
    window.set_title(title)
    app = application if application is not None else Gtk.Application.get_default()
    if app is not None:
        window.set_application(app)


def register_shell_popup(window: Gtk.Window, parent: Gtk.Window) -> None:
    """Attach popup windows to the same Gtk.Application as the bar."""
    application = parent.get_application()
    if application is not None:
        window.set_application(application)


def configure_interactive_popup(window: Gtk.Window) -> None:
    """GTK defaults for interactive shell popups (may accept keyboard focus)."""
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_accept_focus(True)
    window.set_focus_on_map(True)
    window.set_keep_above(True)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    window.add_events(
        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
    )


def configure_osd_window(window: Gtk.Window) -> None:
    """GTK defaults for informational OSD: never steal focus or keyboard."""
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_accept_focus(False)
    window.set_can_focus(False)
    window.set_focus_on_map(False)
    window.set_keep_above(True)
    window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
    try:
        window.set_urgency_hint(False)
    except Exception:
        pass


def configure_passive_popup(window: Gtk.Window) -> None:
    """GTK defaults for clickable popups that must never take keyboard focus."""
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_accept_focus(False)
    window.set_can_focus(False)
    window.set_focus_on_map(False)
    window.set_keep_above(True)
    window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)


def anchor_button_geometry(anchor: Gtk.Widget) -> AnchorButtonGeometry | None:
    """Root/screen geometry for a bar button used as a popup anchor."""
    toplevel = anchor.get_toplevel()
    if not toplevel.get_realized():
        return None

    toplevel_window = toplevel.get_window()
    if toplevel_window is None:
        return None

    translated = anchor.translate_coordinates(toplevel, 0, 0)
    if translated is None:
        return None

    anchor_x, anchor_y = translated
    origin = toplevel_window.get_origin()
    if len(origin) == 3:
        _, root_x, root_y = origin
    else:
        root_x, root_y = origin

    allocation = anchor.get_allocation()
    left = int(root_x + anchor_x)
    top = int(root_y + anchor_y)
    width = int(allocation.width)
    height = int(allocation.height)
    return AnchorButtonGeometry(
        left=left,
        top=top,
        width=width,
        height=height,
        center_x=left + width // 2,
        bottom=top + height,
    )


def popup_window_size(window: Gtk.Window) -> tuple[int, int]:
    """Best-effort popup size after GTK layout."""
    allocation = window.get_allocation()
    width = int(allocation.width)
    height = int(allocation.height)
    if width <= 1:
        width = int(window.get_preferred_width()[0])
    if height <= 1:
        height = int(window.get_preferred_height()[1])
    return max(width, 1), max(height, 1)


def compute_popup_top_left(
    *,
    button_center_x: int,
    button_bottom: int,
    popup_width: int,
    popup_height: int,
    offset: int,
    fixed_top: int | None = None,
    monitor: MonitorRect | None = None,
    margin: int = POPUP_EDGE_MARGIN,
) -> tuple[int, int]:
    """Top-left popup coordinates: centered on the anchor, top below the button."""
    popup_left = button_center_x - popup_width // 2
    popup_top = fixed_top if fixed_top is not None else button_bottom + offset

    if monitor is not None:
        mon_left = monitor.x
        mon_top = monitor.y
        mon_right = monitor.x + monitor.width
        mon_bottom = monitor.y + monitor.height

        popup_left = max(
            mon_left + margin,
            min(popup_left, mon_right - popup_width - margin),
        )
        popup_top = max(
            mon_top + margin,
            min(popup_top, mon_bottom - popup_height - margin),
        )

    return int(popup_left), int(popup_top)


def monitor_containing_point(x: int, y: int) -> MonitorRect | None:
    """Return the Hyprland monitor that contains ``(x, y)``."""
    monitors = query_hyprland_monitors()
    for monitor in monitors:
        if (
            monitor.x <= x < monitor.x + monitor.width
            and monitor.y <= y < monitor.y + monitor.height
        ):
            return monitor
    return monitors[0] if monitors else None


def query_hyprland_monitors() -> tuple[MonitorRect, ...]:
    """Read monitor geometry from Hyprland."""
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

    if not isinstance(payload, list):
        return ()

    monitors: list[MonitorRect] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        try:
            monitors.append(
                MonitorRect(
                    x=int(entry["x"]),
                    y=int(entry["y"]),
                    width=int(entry["width"]),
                    height=int(entry["height"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(monitors)


def position_popup_below_anchor(
    window: Gtk.Window,
    anchor: Gtk.Widget,
    *,
    title: str,
    offset: int,
    fixed_top: int | None = None,
    margin: int = POPUP_EDGE_MARGIN,
    extra_y_offset: int = 0,
) -> int | None:
    """Position a popup from anchor geometry and popup size; return the top edge used."""
    geometry = anchor_button_geometry(anchor)
    if geometry is None:
        return None

    popup_width, popup_height = popup_window_size(window)
    monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
    popup_left, popup_top = compute_popup_top_left(
        button_center_x=geometry.center_x,
        button_bottom=geometry.bottom,
        popup_width=popup_width,
        popup_height=popup_height,
        offset=offset + extra_y_offset,
        fixed_top=(fixed_top + extra_y_offset) if fixed_top is not None else None,
        monitor=monitor,
        margin=margin,
    )
    reposition_popup(window, title=title, x=popup_left, y=popup_top)
    return popup_top


def apply_popup_position(window: Gtk.Window, *, title: str, x: int, y: int) -> None:
    """Position a popup; on Wayland toplevels GTK move is ignored by the compositor."""
    reposition_popup(window, title=title, x=x, y=y)
    if is_wayland_session():
        schedule_hyprland_popup_move(title, int(x), int(y))


def reposition_popup(window: Gtk.Window, *, title: str, x: int, y: int) -> None:
    window.move(int(x), int(y))
    if is_wayland_session():
        schedule_hyprland_popup_move(title, int(x), int(y))


def schedule_hyprland_popup_move(title: str, x: int, y: int) -> None:
    """Move a floating shell popup through Hyprland after map."""

    def move_once() -> bool:
        _hyprland_move_popup(title, x, y)
        return False

    move_once()
    for delay_ms in (50, 100, 150):
        GLib.timeout_add(delay_ms, move_once)


def _hyprland_move_popup(title: str, x: int, y: int) -> None:
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    expression = (
        f'hl.dsp.window.move({{ window = "title:^{safe_title}$", '
        f"x = {x}, y = {y}, relative = false }})"
    )
    subprocess.run(
        ["hyprctl", "dispatch", expression],
        check=False,
        capture_output=True,
        text=True,
        timeout=1.0,
    )


def schedule_popup_position(callback: Callable[[], bool | None]) -> None:
    """Run positioning after map and retry once the compositor finishes placement."""

    def run_once() -> bool:
        callback()
        return False

    GLib.idle_add(run_once)
    GLib.timeout_add(50, run_once)
    GLib.timeout_add(150, run_once)
