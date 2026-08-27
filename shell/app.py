#!/usr/bin/env python3
"""Process entry point that wires shell services to visual modules."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, Gio, Gtk, GtkLayerShell

from .config import PERSISTENT_WORKSPACES, TOP_MARGIN
from .controllers.control_center import ControlCenterController
from .controllers.media import MediaController
from .controllers.shell_compact import ShellCompactController
from .controllers.volume_osd import VolumeOsdController
from .controllers.workspace_interaction import WorkspaceInteractionController
from .eventbus import EventBus
from .layout import ShellLayout
from .servicios.audio.audio import AudioService
from .servicios.audio.audio_visualizer import AudioVisualizerService
from .servicios.escritorio.hyprland import HyprlandService
from .servicios.multimedia.media import MediaService
from .servicios.red.network import NetworkService
from .servicios.notificaciones.notifications import NotificationService
from .servicios.energia.power import PowerService
from .servicios.sistema.system import SystemStatsService
from .servicios.bandeja.tray import SystemTrayService
from .widgets.barra.active_window import ActiveWindowWidget
from .widgets.barra.clock import ClockWidget
from .widgets.barra.ethernet import EthernetWidget
from .widgets.barra.notifications import NotificationsWidget
from .widgets.barra.power import PowerWidget
from .widgets.barra.stats import StatsWidget
from .widgets.barra.tray import SystemTrayWidget
from .widgets.barra.workspace import WorkspaceWidget
from .window_identity import APPLICATION_ID, TITLE_BAR, configure_toplevel, init_window_identity

APP_ID = "shell"


class ShellApplication(Gtk.Window):
    """Mounts modules and coordinates state updates without putting IPC in a widget."""

    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_application(application)
        self.event_bus = EventBus(dispatch_on_main=True)
        self.hyprland = HyprlandService(self.event_bus, PERSISTENT_WORKSPACES)
        self.audio_service = AudioService(self.event_bus)
        self.system_stats = SystemStatsService()
        self.power_service = PowerService()
        self.tray_service = SystemTrayService()
        self.notification_service = NotificationService(self.event_bus)
        self.network_service = NetworkService(self.event_bus)
        self.media_service = MediaService(self.event_bus)
        self.audio_visualizer = AudioVisualizerService(
            self.event_bus,
            self.media_service,
            self.audio_service,
        )

        self.set_name(APP_ID)
        configure_toplevel(self, title=TITLE_BAR)
        self.set_decorated(False)
        self.set_resizable(False)
        self._configure_layer_shell()
        self._load_css()

        self.layout = ShellLayout()
        self._bar_host = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._bar_host.get_style_context().add_class("shell-bar-host")
        self._bar_host.set_hexpand(True)
        self._bar_host.pack_start(Gtk.Box(), True, True, 0)
        self._bar_host.pack_start(self.layout, False, False, 0)
        self._bar_host.pack_start(Gtk.Box(), True, True, 0)

        self._overlay = Gtk.Overlay()
        self._overlay.add(self._bar_host)
        self.add(self._overlay)

        self.workspace_widget = WorkspaceWidget(self.event_bus)
        self.layout.center.add(self.workspace_widget)
        self.active_window_widget = ActiveWindowWidget(self.event_bus, self.media_service)
        self.layout.center.add(self.active_window_widget)

        self.stats_widget = StatsWidget(self.system_stats)
        self.layout.right.add(self.stats_widget)
        self.ethernet_widget = EthernetWidget(self.event_bus, self.network_service)
        self.layout.right.add(self.ethernet_widget)
        self.tray_widget = SystemTrayWidget(self.tray_service)
        self.layout.right.add(self.tray_widget)
        self.notifications_widget = NotificationsWidget(
            self.event_bus,
            self.notification_service,
            self,
        )
        self.layout.right.add(self.notifications_widget)
        self.clock_widget = ClockWidget()
        self.layout.right.add(self.clock_widget)
        self.power_widget = PowerWidget(self.power_service, self, self.event_bus)
        self.layout.right.add(self.power_widget)
        self.control_center_controller = ControlCenterController(
            self.event_bus,
            self.network_service,
            self.ethernet_widget,
            self.power_widget,
            self,
        )

        self.workspace_controller = WorkspaceInteractionController(
            self.event_bus,
            self.audio_service,
            self.workspace_widget,
            self,
        )

        self.media_controller = MediaController(
            self.event_bus,
            self.media_service,
            self.active_window_widget,
            self,
        )

        self.volume_osd_controller = VolumeOsdController(
            self.event_bus,
            self.audio_service,
            self,
        )

        self.compact_controller = ShellCompactController(
            self.event_bus,
            self.hyprland,
            shell_window=self,
            hide_in_compact=(),
            compact_adapters=(
                self.clock_widget,
                self.ethernet_widget,
                self.tray_widget,
                self.notifications_widget,
                self.power_widget,
            ),
        )

        self.connect("destroy", self._on_destroy)

        self.hyprland.start()
        self.audio_service.start()
        self.volume_osd_controller.start()
        self.network_service.start()
        self.media_service.start()
        self.audio_visualizer.start()
        self.notification_service.start()
        self.show_all()

    # Public API for keybindings or external triggers
    def toggle_workspace_panel(self, workspace_id: int) -> None:
        self.workspace_controller.toggle_workspace_panel(workspace_id)

    def close_workspace_panel(self) -> None:
        self.workspace_controller.close_workspace_panel()

    def close_control_center(self) -> None:
        self.control_center_controller.close_popup()

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, APP_ID)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, TOP_MARGIN)
        GtkLayerShell.auto_exclusive_zone_enable(self)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

    @staticmethod
    def _load_css() -> None:
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_destroy(self, *_args) -> None:
        # No service may enqueue UI work once destruction starts.
        self.event_bus.close()
        self.volume_osd_controller.close()
        self.audio_service.close()
        self.network_service.close()
        self.media_service.close()
        self.audio_visualizer.close()
        self.notification_service.close()
        self.hyprland.close()


class ShellGtkApplication(Gtk.Application):
    """Owns the GTK main loop and keeps ShellApplication alive for GApplication."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._shell_window: ShellApplication | None = None

    def do_activate(self) -> None:
        if self._shell_window is None:
            self._shell_window = ShellApplication(self)


def main() -> None:
    init_window_identity()
    app = ShellGtkApplication()
    print("APP CREADA")
    status = app.run(sys.argv)
    print("APP TERMINÓ:", status)
