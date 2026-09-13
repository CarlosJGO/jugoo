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

from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell

from .config import (
    ACTIVE_THEME,
    HYPRLAND_THEME_EXPORT_PATH,
    PERSISTENT_WORKSPACES,
    TOP_MARGIN,
)
from .controllers.applications import ApplicationsController
from .controllers.control_center import ControlCenterController
from .controllers.pickers import PickersController
from .controllers.media import MediaController
from .controllers.settings import SettingsController
from .controllers.shell_compact import ShellCompactController
from .controllers.volume_osd import VolumeOsdController
from .controllers.workspace_interaction import WorkspaceInteractionController
from .eventbus import EventBus
from .identity import project_root
from .layout import ShellLayout
from .runtime_paths import settings_path
from .servicios.aplicaciones.applications import ApplicationsService
from .servicios.portapapeles.servicio import ClipboardService
from .servicios.audio.audio import AudioService
from .servicios.audio.audio_visualizer import AudioVisualizerService
from .servicios.escritorio.hyprland import HyprlandService
from .servicios.multimedia.media import MediaService
from .servicios.red.network import NetworkService
from .servicios.notificaciones.notifications import NotificationService
from .servicios.energia.power import PowerService
from .servicios.sistema.system import SystemStatsService
from .servicios.bandeja.tray import SystemTrayService
from .servicios.tareas.briefing import StartupTaskBriefing
from .servicios.tareas.presencia import TaskWatcherBridge
from .servicios.tareas.tasks import TasksService
from .servicios.tareas.vigilancia.sesion import ensure_task_watcher_service
from .servicios.teclado.actividad import KeyboardActivityService
from .settings.manager import SettingsManager
from .widgets.barra.active_window import ActiveWindowWidget
from .widgets.barra.clock import ClockWidget
from .widgets.barra.ethernet import EthernetWidget
from .widgets.barra.keyboard_cat import KeyboardCatWidget
from .widgets.barra.notifications import NotificationsWidget
from .widgets.barra.pinned_apps import PinnedAppsWidget
from .widgets.barra.power import PowerWidget
from .widgets.barra.settings import SettingsWidget
from .widgets.barra.stats import StatsWidget
from .widgets.barra.tasks import TasksWidget
from .widgets.barra.tray import SystemTrayWidget
from .widgets.barra.workspace import WorkspaceWidget
from .ui.starfield import StarfieldBackground
from .ui.theme import ThemeManager
from .window_identity import APPLICATION_ID, TITLE_BAR, configure_toplevel, init_window_identity

APP_ID = "shell"


def _gdk_monitor() -> Gdk.Monitor | None:
    """GDK often leaves the only output unmarked as primary on wlroots."""
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitor = display.get_primary_monitor()
    if monitor is None and display.get_n_monitors() > 0:
        monitor = display.get_monitor(0)
    return monitor


class ShellApplication(Gtk.Window):
    """Mounts modules and coordinates state updates without putting IPC in a widget."""

    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_application(application)
        self.event_bus = EventBus(dispatch_on_main=True)
        root = project_root()
        self.theme_manager = ThemeManager(
            self.event_bus,
            themes_dir=root / "themes",
            structural_css_path=Path(__file__).with_name("style.css"),
            active_name=ACTIVE_THEME,
            hypr_export_path=Path(HYPRLAND_THEME_EXPORT_PATH).expanduser(),
        )
        self.settings_manager = SettingsManager(
            self.event_bus,
            path=settings_path(),
            theme_setter=self.theme_manager.set_theme,
            theme_choices=self._theme_choices,
        )
        self.hyprland = HyprlandService(self.event_bus, PERSISTENT_WORKSPACES)
        self.applications = ApplicationsService(self.event_bus)
        self.clipboard_service = ClipboardService(self.event_bus)
        self.audio_service = AudioService(self.event_bus)
        self.system_stats = SystemStatsService()
        self.power_service = PowerService()
        self.tray_service = SystemTrayService()
        self.notification_service = NotificationService(self.event_bus)
        self.tasks_service = TasksService(self.event_bus)
        self.task_watcher_bridge = TaskWatcherBridge(self.event_bus)
        self._startup_briefing = StartupTaskBriefing(
            tasks_service=self.tasks_service,
            notifications=self.notification_service,
        )
        self.network_service = NetworkService(self.event_bus)
        self.media_service = MediaService(self.event_bus)
        self.keyboard_activity = KeyboardActivityService(self.event_bus)
        self.audio_visualizer = AudioVisualizerService(
            self.event_bus,
            self.media_service,
            self.audio_service,
            self.theme_manager,
        )

        self.set_name(APP_ID)
        configure_toplevel(self, title=TITLE_BAR)
        self.set_decorated(False)
        # Layer-shell surfaces anchored to LEFT+RIGHT must remain resizable:
        # with resizable=False GTK rejects the compositor-assigned width and
        # falls back to the natural size, leaving the bar short of the edges.
        self.set_resizable(True)
        self._configure_layer_shell()
        self.theme_manager.start()

        self.layout = ShellLayout()
        monitor = _gdk_monitor()
        if monitor is not None:
            self.layout.set_size_request(monitor.get_geometry().width, -1)

        # Starfield paints the void; layout modules sit as its child above the stars.
        self._bar_host = StarfieldBackground(
            self.event_bus,
            corner_radius=0.0,
            draw_rim=True,
        )
        self._bar_host.get_style_context().add_class("shell-bar-host")
        self._bar_host.set_hexpand(True)
        self._bar_host.set_halign(Gtk.Align.FILL)
        self._bar_host.add(self.layout)

        self._overlay = Gtk.Overlay()
        self._overlay.set_hexpand(True)
        self._overlay.set_halign(Gtk.Align.FILL)
        if monitor is not None:
            self._overlay.set_size_request(monitor.get_geometry().width, -1)
        self._overlay.add(self._bar_host)
        self.add(self._overlay)
        if monitor is not None:
            self.set_size_request(monitor.get_geometry().width, -1)

        self.workspace_widget = WorkspaceWidget(self.event_bus)
        self.layout.center.add(self.workspace_widget)

        self.active_window_widget = ActiveWindowWidget(self.event_bus, self.media_service)
        self.layout.left.set_halign(Gtk.Align.START)
        self.layout.left.add(self.active_window_widget)

        self.pinned_apps_widget = PinnedAppsWidget(self.event_bus, self)
        self.layout.left.add(self.pinned_apps_widget)
        self.keyboard_cat_widget = KeyboardCatWidget(self.event_bus, self._bar_host)
        self.layout.left.add(self.keyboard_cat_widget)

        self.ethernet_widget = EthernetWidget(self.event_bus, self.network_service)
        self.tray_widget = SystemTrayWidget(self.tray_service)
        self.layout.right.add(self.tray_widget)
        self.notifications_widget = NotificationsWidget(
            self.event_bus,
            self.notification_service,
            self,
        )
        self.layout.right.add(self.notifications_widget)
        self.layout.right.add(self.ethernet_widget)
        self.stats_widget = StatsWidget(self.system_stats, self, self.event_bus)
        self.layout.right.add(self.stats_widget)
        self.tasks_widget = TasksWidget(self.event_bus, self.tasks_service, self)
        self.layout.right.add(self.tasks_widget)
        self.clock_widget = ClockWidget(self.event_bus, self.tasks_service)
        self.layout.right.add(self.clock_widget)
        self.settings_widget = SettingsWidget(self.toggle_settings)
        self.layout.right.add(self.settings_widget)
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

        self.applications_controller = ApplicationsController(
            self.event_bus,
            self.applications,
            self.hyprland,
            self,
            self.settings_manager,
            system_stats=self.system_stats,
        )
        self.pickers_controller = PickersController(
            self.event_bus,
            self.clipboard_service,
            self,
            close_launcher=self.applications_controller.close_launcher,
            hyprland=self.hyprland,
        )
        self.settings_controller = SettingsController(
            open_settings=self.applications_controller.open_settings,
            close_center=self.applications_controller.close_control_center,
        )
        self.settings_manager.set_volume_osd_delay_hook(
            self.volume_osd_controller.set_hide_delay_ms
        )
        # Load persisted overrides after live hooks exist so first apply is complete.
        self.settings_manager.start()

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
                self.tasks_widget,
                self.settings_widget,
                self.power_widget,
                self.pinned_apps_widget,
            ),
        )

        self.connect("destroy", self._on_destroy)

        self.applications.start()
        self.clipboard_service.start()
        self.hyprland.start()
        self.audio_service.start()
        self.volume_osd_controller.start()
        self.network_service.start()
        self.media_service.start()
        self.audio_visualizer.start()
        self.notification_service.start()
        self.tasks_service.start()
        self.task_watcher_bridge.start()
        self.keyboard_activity.start()
        GLib.idle_add(self._ensure_task_watcher)
        GLib.timeout_add(700, self._startup_briefing.schedule)
        self.show_all()
        GLib.idle_add(self.applications_controller.warm)
        GLib.timeout_add(120, self.pickers_controller.warm)

    # Public API for keybindings or external triggers
    def toggle_workspace_panel(self, workspace_id: int) -> None:
        self.workspace_controller.toggle_workspace_panel(workspace_id)

    def close_workspace_panel(self) -> None:
        self.workspace_controller.close_workspace_panel()

    def close_control_center(self) -> None:
        self.control_center_controller.close_popup()

    def toggle_control_center(self) -> None:
        self.control_center_controller.toggle_full_control_center()

    def toggle_notifications(self) -> None:
        self.notifications_widget.toggle_popup()

    def toggle_session(self) -> None:
        self.power_widget.toggle_menu()

    def toggle_launcher(self) -> None:
        self.pickers_controller.close_pickers()
        self.applications_controller.toggle_launcher()

    def toggle_clipboard_picker(self) -> None:
        self.applications_controller.close_control_center()
        self.pickers_controller.toggle_clipboard()

    def toggle_emoji_picker(self) -> None:
        self.applications_controller.close_control_center()
        self.pickers_controller.toggle_emoji()

    def toggle_media_popup(self) -> None:
        self.media_controller.toggle_popup()

    def toggle_settings(self) -> None:
        self.pickers_controller.close_pickers()
        self.settings_controller.toggle()

    def open_tasks_panel(self) -> None:
        self.tasks_service.request_panel()

    def reload_theme(self) -> None:
        self.theme_manager.reload_current()

    def _theme_choices(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (name, name.replace("-", " ").replace("_", " ").title())
            for name in self.theme_manager.available_themes
        )

    def _ensure_task_watcher(self) -> bool:
        ensure_task_watcher_service()
        return False

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

    def _on_destroy(self, *_args) -> None:
        # No service may enqueue UI work once destruction starts.
        self.theme_manager.close()
        self.settings_manager.close()
        self.event_bus.close()
        self.volume_osd_controller.close()
        self.audio_service.close()
        self.network_service.close()
        self.media_service.close()
        self.audio_visualizer.close()
        self.notification_service.close()
        self._startup_briefing.close()
        self.task_watcher_bridge.close()
        self.tasks_service.close()
        self.settings_controller.close()
        self.pickers_controller.close()
        self.applications_controller.close_launcher()
        self.clipboard_service.close()
        self.applications.close()
        self.keyboard_activity.close()
        self.hyprland.close()


class ShellGtkApplication(Gtk.Application):
    """Owns the GTK main loop and keeps ShellApplication alive for GApplication."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._shell_window: ShellApplication | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        from .actions import ACTIONS

        for action in ACTIONS:
            gio_action = Gio.SimpleAction.new(action.name, None)
            gio_action.connect("activate", self._on_named_action, action.name)
            self.add_action(gio_action)
            # Legacy action ids used by older callers (task watcher, scripts).
            for flag in action.legacy_flags:
                legacy_name = flag.lstrip("-")
                if legacy_name == action.name:
                    continue
                legacy = Gio.SimpleAction.new(legacy_name, None)
                legacy.connect("activate", self._on_named_action, action.name)
                self.add_action(legacy)

    def do_activate(self) -> None:
        if self._shell_window is None:
            self._shell_window = ShellApplication(self)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        from .actions import (
            UNSUPPORTED,
            dispatch_action,
            format_actions_help,
            resolve_actions_from_argv,
        )

        arguments = [str(item) for item in command_line.get_arguments()[1:]]
        if "--list-actions" in arguments or (
            arguments[:1] == ["action"]
            and (len(arguments) == 1 or arguments[1:2] == ["list"])
        ):
            print(format_actions_help())
            return 0

        names = resolve_actions_from_argv(arguments)
        if not names:
            self.activate()
            return 0

        self.activate()
        shell = self._shell_window
        if shell is None:
            print("jugoo: shell window is not ready", file=sys.stderr)
            return 1

        status = 0
        for name in names:
            if name in UNSUPPORTED:
                print(f"jugoo: {UNSUPPORTED[name]}", file=sys.stderr)
                status = 2
                continue
            error = dispatch_action(name, shell)
            if error:
                print(f"jugoo: {error}", file=sys.stderr)
                status = 2
        return status

    def _on_named_action(self, _action: Gio.SimpleAction, _param, name: str) -> None:
        from .actions import UNSUPPORTED, dispatch_action

        self.activate()
        shell = self._shell_window
        if shell is None:
            return
        if name in UNSUPPORTED:
            print(f"jugoo: {UNSUPPORTED[name]}", file=sys.stderr)
            return
        error = dispatch_action(name, shell)
        if error:
            print(f"jugoo: {error}", file=sys.stderr)


def main() -> None:
    if "--install" in sys.argv[1:]:
        from .desktop_install import install_identity

        raise SystemExit(install_identity())
    if "--uninstall" in sys.argv[1:]:
        from .desktop_install import uninstall_identity

        raise SystemExit(uninstall_identity())
    if "--task-watcher" in sys.argv[1:]:
        from .servicios.tareas.vigilancia import run_task_watcher

        raise SystemExit(run_task_watcher())
    if "--list-actions" in sys.argv[1:] or (
        sys.argv[1:2] == ["action"]
        and (len(sys.argv) == 2 or sys.argv[2:3] == ["list"])
    ):
        # Allow listing without bringing up the full shell / claiming the bus.
        from .actions import format_actions_help

        print(format_actions_help())
        raise SystemExit(0)

    init_window_identity()
    app = ShellGtkApplication()
    raise SystemExit(app.run(sys.argv))
