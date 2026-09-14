"""Focus-first activation for the app that posted a notification."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from ...models import (
    ApplicationsSnapshot,
    DesktopApplication,
    HyprlandSnapshot,
    NotificationSnapshot,
    Window,
    application_identity_keys,
    next_window_to_focus,
    normalize_desktop_id,
    snapshot_windows,
    windows_for_application,
)


def notification_identity_keys(snapshot: NotificationSnapshot) -> frozenset[str]:
    """Loose identity keys from desktop-entry / app_name for window matching."""
    keys: set[str] = set()
    desktop = normalize_desktop_id(snapshot.desktop_entry or "").casefold()
    if desktop:
        keys.add(desktop)
        if "." in desktop:
            keys.add(desktop.rsplit(".", 1)[-1])
        stem = Path(snapshot.desktop_entry).stem.casefold()
        if stem:
            keys.add(stem)
    name = (snapshot.app_name or "").strip().casefold()
    if name:
        keys.add(name)
        keys.add(name.replace(" ", "-"))
        keys.add(name.replace(" ", ""))
        keys.add(name.replace(" ", "_"))
    return frozenset(key for key in keys if key)


def resolve_notification_app_id(
    snapshot: NotificationSnapshot,
    applications: ApplicationsSnapshot,
) -> str | None:
    """Map a notification sender to a catalog desktop id when possible."""
    candidates: list[str] = []
    desktop = normalize_desktop_id(snapshot.desktop_entry or "")
    if desktop:
        candidates.append(desktop)
    app_name = (snapshot.app_name or "").strip()
    if app_name and normalize_desktop_id(app_name) not in {
        normalize_desktop_id(item) for item in candidates
    }:
        candidates.append(app_name)

    for candidate in candidates:
        found = applications.app_by_id(candidate)
        if found is not None:
            return found.id

    identity = notification_identity_keys(snapshot)
    name_key = app_name.casefold() if app_name else ""
    for application in applications.applications:
        app_keys = application_identity_keys(application)
        if identity & app_keys:
            return application.id
        if name_key and (
            application.name.casefold() == name_key
            or application.id.casefold() == name_key
        ):
            return application.id

    return candidates[0] if candidates else None


def windows_for_notification_keys(
    windows: Sequence[Window],
    keys: frozenset[str],
) -> tuple[Window, ...]:
    if not keys:
        return ()
    matches: list[Window] = []
    for window in windows:
        class_key = window.app_class.casefold().strip()
        app_name = window.application_name.casefold().strip()
        if class_key and class_key in keys:
            matches.append(window)
            continue
        if app_name and app_name in keys:
            matches.append(window)
            continue
        for key in keys:
            if len(key) < 4:
                continue
            if class_key and (key in class_key or class_key in key):
                matches.append(window)
                break
            if app_name and (key in app_name or app_name in key):
                matches.append(window)
                break
    return tuple(matches)


def activate_notification_sender(
    snapshot: NotificationSnapshot,
    *,
    applications: ApplicationsSnapshot,
    hyprland: HyprlandSnapshot | None,
    focus_window: Callable[[str], None],
    activate_app: Callable[[str], None],
) -> bool:
    """Bring the sender forward: focus existing window (and its WS), else activate/launch.

    Returns True when an existing mapped window was focused.
    """
    windows = snapshot_windows(hyprland)
    active_address = ""
    if hyprland is not None and hyprland.active_window is not None:
        active_address = hyprland.active_window.address or ""

    app_id = resolve_notification_app_id(snapshot, applications)
    application: DesktopApplication | None = (
        applications.app_by_id(app_id) if app_id else None
    )

    matches: tuple[Window, ...] = ()
    if application is not None:
        matches = windows_for_application(application, windows)
    if not matches:
        matches = windows_for_notification_keys(windows, notification_identity_keys(snapshot))

    target = next_window_to_focus(matches, active_address)
    if target is not None and target.address:
        focus_window(target.address)
        return True

    if app_id:
        activate_app(app_id)
        return False

    _launch_desktop_candidates(snapshot)
    return False


def _launch_desktop_candidates(snapshot: NotificationSnapshot) -> None:
    """Last-resort gtk-launch when the sender is not in the catalog."""
    candidates: list[str] = []
    desktop = normalize_desktop_id(snapshot.desktop_entry or "")
    if desktop:
        candidates.append(desktop)
    app_name = (snapshot.app_name or "").strip()
    if app_name and app_name not in candidates:
        candidates.append(app_name)

    for ident in candidates:
        commands: list[tuple[str, ...]] = []
        if shutil.which("uwsm"):
            commands.append(("uwsm", "app", "--", "gtk-launch", ident))
        commands.append(("gtk-launch", ident))
        for command in commands:
            try:
                subprocess.Popen(command)
                return
            except (OSError, subprocess.SubprocessError):
                continue
