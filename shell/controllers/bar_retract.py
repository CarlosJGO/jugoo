"""Retract the top bar when floating windows push into its strip."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GLib", "2.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import GLib, Gtk, GtkLayerShell

from .. import config as shell_config
from ..eventbus import EventBus
from ..identity import WAYLAND_APP_ID
from ..models import FloatingClient
from ..servicios.escritorio.bar_retract import compute_bar_retract_px, step_retract_px
from ..servicios.escritorio.hyprland import (
    ACTIVE_WINDOW_CHANGED,
    FLOATING_LAYOUT_CHANGED,
    FULLSCREEN_CHANGED,
    MONITOR_CHANGED,
    WINDOW_CLOSED,
    WINDOW_OPENED,
    WORKSPACE_CHANGED,
    HyprlandService,
)


class BarRetractController:
    """Pull the bar upward just enough to clear floating windows beneath it."""

    def __init__(
        self,
        event_bus: EventBus,
        hyprland: HyprlandService,
        shell_window: Gtk.Window,
    ) -> None:
        self._event_bus = event_bus
        self._hyprland = hyprland
        self._shell = shell_window
        self._current_retract = 0.0
        self._target_retract = 0.0
        self._poll_source_id = 0
        self._anim_source_id = 0
        self._applied_retract_px = 0
        self._auto_exclusive = True

        self._event_bus.subscribe(FLOATING_LAYOUT_CHANGED, self._on_layout_hint)
        self._event_bus.subscribe(WORKSPACE_CHANGED, self._on_layout_hint)
        self._event_bus.subscribe(WINDOW_OPENED, self._on_layout_hint)
        self._event_bus.subscribe(WINDOW_CLOSED, self._on_layout_hint)
        self._event_bus.subscribe(ACTIVE_WINDOW_CHANGED, self._on_layout_hint)
        self._event_bus.subscribe(FULLSCREEN_CHANGED, self._on_layout_hint)
        self._event_bus.subscribe(MONITOR_CHANGED, self._on_layout_hint)

    def start(self) -> None:
        GLib.idle_add(self._evaluate)

    def close(self) -> None:
        self._event_bus.unsubscribe(FLOATING_LAYOUT_CHANGED, self._on_layout_hint)
        self._event_bus.unsubscribe(WORKSPACE_CHANGED, self._on_layout_hint)
        self._event_bus.unsubscribe(WINDOW_OPENED, self._on_layout_hint)
        self._event_bus.unsubscribe(WINDOW_CLOSED, self._on_layout_hint)
        self._event_bus.unsubscribe(ACTIVE_WINDOW_CHANGED, self._on_layout_hint)
        self._event_bus.unsubscribe(FULLSCREEN_CHANGED, self._on_layout_hint)
        self._event_bus.unsubscribe(MONITOR_CHANGED, self._on_layout_hint)
        self._stop_poll()
        self._stop_anim()
        self._current_retract = 0.0
        self._target_retract = 0.0
        self._apply_retract(0)

    def _on_layout_hint(self, _payload: object) -> None:
        GLib.idle_add(self._evaluate)

    def _evaluate(self) -> bool:
        if not shell_config.BAR_RETRACT_ENABLED:
            self._stop_poll()
            self._set_target(0)
            return False

        clients = self._relevant_clients()
        self._sync_poll(clients)
        target = self._compute_target(clients)
        self._set_target(target)
        return False

    def _relevant_clients(self) -> tuple[FloatingClient, ...]:
        active_workspace = self._hyprland.active_workspace_id
        clients = self._hyprland.floating_clients
        if active_workspace == 0 and not clients:
            return ()
        return tuple(
            client
            for client in clients
            if client.workspace_id == active_workspace or client.pinned
        )

    def _compute_target(self, clients: tuple[FloatingClient, ...]) -> int:
        geometry = self._bar_monitor_geometry()
        if geometry is None:
            return 0
        bar_left, bar_top, bar_width, bar_height = geometry
        if bar_height <= 0:
            return 0
        return compute_bar_retract_px(
            bar_top=bar_top + int(shell_config.TOP_MARGIN),
            bar_height=bar_height,
            bar_left=bar_left,
            bar_width=bar_width,
            gap_px=shell_config.BAR_RETRACT_GAP_PX,
            clients=clients,
            ignore_classes=(WAYLAND_APP_ID, "com.jugoo.Shell"),
        )

    def _bar_monitor_geometry(self) -> tuple[int, int, int, int] | None:
        window = self._shell.get_window()
        display = self._shell.get_display()
        if window is None or display is None:
            return None
        monitor = display.get_monitor_at_window(window)
        if monitor is None:
            return None
        geo = monitor.get_geometry()
        # Prefer the shell's allocated height (actual bar chrome), not monitor height.
        bar_height = max(1, int(self._shell.get_allocated_height()))
        return int(geo.x), int(geo.y), int(geo.width), bar_height

    def _sync_poll(self, clients: tuple[FloatingClient, ...]) -> None:
        # Keep polling while any float exists on this workspace so live drag feels smooth.
        if clients:
            self._start_poll()
        else:
            self._stop_poll()

    def _start_poll(self) -> None:
        if self._poll_source_id:
            return
        self._poll_source_id = GLib.timeout_add(
            shell_config.BAR_RETRACT_POLL_MS,
            self._poll_tick,
        )

    def _stop_poll(self) -> None:
        if self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = 0

    def _poll_tick(self) -> bool:
        self._hyprland.poll_floating_layout()
        clients = self._relevant_clients()
        if not clients and self._target_retract <= 0 and self._current_retract <= 0:
            self._poll_source_id = 0
            return False
        self._set_target(self._compute_target(clients))
        return True

    def _set_target(self, target: int) -> None:
        target = max(0, int(target))
        if target == int(round(self._target_retract)):
            if abs(self._current_retract - target) < 0.5:
                return
        self._target_retract = float(target)
        self._start_anim()

    def _start_anim(self) -> None:
        if self._anim_source_id:
            return
        self._anim_source_id = GLib.timeout_add(
            shell_config.BAR_RETRACT_ANIM_TICK_MS,
            self._anim_tick,
        )

    def _stop_anim(self) -> None:
        if self._anim_source_id:
            GLib.source_remove(self._anim_source_id)
            self._anim_source_id = 0

    def _anim_tick(self) -> bool:
        nxt = step_retract_px(
            current=self._current_retract,
            target=self._target_retract,
        )
        self._current_retract = nxt
        self._apply_retract(int(round(nxt)))
        if abs(self._current_retract - self._target_retract) < 0.5:
            self._current_retract = self._target_retract
            self._apply_retract(int(round(self._target_retract)))
            self._anim_source_id = 0
            return False
        return True

    def _apply_retract(self, retract_px: int) -> None:
        bar_height = max(1, int(self._shell.get_allocated_height()))
        retract_px = max(0, min(retract_px, bar_height))
        if retract_px == self._applied_retract_px and (
            (retract_px == 0) == self._auto_exclusive
        ):
            return
        self._applied_retract_px = retract_px
        top_margin = int(shell_config.TOP_MARGIN) - retract_px
        GtkLayerShell.set_margin(self._shell, GtkLayerShell.Edge.TOP, top_margin)
        if retract_px <= 0:
            GtkLayerShell.auto_exclusive_zone_enable(self._shell)
            self._auto_exclusive = True
            return
        visible = max(0, bar_height - retract_px)
        GtkLayerShell.set_exclusive_zone(self._shell, visible)
        self._auto_exclusive = False
