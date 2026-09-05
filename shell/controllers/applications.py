"""Coordinates dock clicks, launcher toggle, and Hyprland window focus."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ..eventbus import EventBus
from ..models import (
    ApplicationsSnapshot,
    next_window_to_focus,
    snapshot_windows,
    windows_for_application,
)
from ..popup_handle import PopupHandle
from ..servicios.aplicaciones.applications import (
    APP_ACTIVATE_REQUESTED,
    APP_PIN_TOGGLE_REQUESTED,
    APPLICATIONS_CHANGED,
    LAUNCHER_TOGGLE_REQUESTED,
    ApplicationsService,
)
from ..servicios.escritorio.hyprland import WINDOW_FOCUS_REQUESTED, HyprlandService
from ..widgets.aplicaciones.launcher import AppLauncherWindow


class ApplicationsController:
    """Dock/launcher share ApplicationsService; Hyprland owns window focus."""

    def __init__(
        self,
        event_bus: EventBus,
        applications: ApplicationsService,
        hyprland: HyprlandService,
        shell_window: Gtk.Window,
    ) -> None:
        self._event_bus = event_bus
        self._applications = applications
        self._hyprland = hyprland
        self._shell_window = shell_window
        self._launcher = PopupHandle(
            lambda: AppLauncherWindow(
                shell_window,
                on_launch=self._activate_application,
                on_pin_toggle=self._toggle_pin,
                on_refresh=self._applications.refresh_catalog,
            )
        )

        event_bus.subscribe(APP_ACTIVATE_REQUESTED, self._on_activate_requested)
        event_bus.subscribe(LAUNCHER_TOGGLE_REQUESTED, self._on_launcher_toggle)
        event_bus.subscribe(APPLICATIONS_CHANGED, self._on_applications_changed)

    def close_launcher(self) -> None:
        launcher = self._launcher.maybe
        if launcher is not None:
            launcher.close_launcher()

    def toggle_launcher(self) -> None:
        self._launcher.get().toggle_launcher()

    def _on_launcher_toggle(self, _payload: object) -> None:
        GLib.idle_add(self.toggle_launcher)

    def _on_applications_changed(self, snapshot: ApplicationsSnapshot) -> None:
        launcher = self._launcher.maybe
        if launcher is None or not launcher.get_visible():
            return
        GLib.idle_add(launcher.set_snapshot, snapshot)

    def _on_activate_requested(self, app_id: object) -> None:
        if isinstance(app_id, str):
            self._activate_application(app_id)

    def _toggle_pin(self, app_id: str) -> None:
        self._event_bus.emit(APP_PIN_TOGGLE_REQUESTED, app_id)

    def _activate_application(self, app_id: str) -> None:
        snapshot = self._applications.snapshot
        application = snapshot.app_by_id(app_id)
        if application is None:
            self._applications.launch(app_id)
            return
        matches = windows_for_application(application, snapshot_windows(self._hyprland.snapshot))
        target = next_window_to_focus(
            matches,
            self._hyprland.snapshot.active_window.address if self._hyprland.snapshot else "",
        )
        if target is not None and target.address:
            self._event_bus.emit(WINDOW_FOCUS_REQUESTED, target.address)
            return
        self._applications.launch(app_id)
