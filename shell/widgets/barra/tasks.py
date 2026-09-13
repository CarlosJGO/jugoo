"""Bar button that opens the tasks Organic Island and shows today's pending count."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ...config import (
    TASKS_COMPACT_ICON_SIZE,
    TASKS_ICON_SIZE,
    TASKS_POPUP_MAX_HEIGHT,
    TASKS_POPUP_WIDTH,
    TASK_WATCHER_POLL_INTERVAL_SEC,
    TASK_WATCHER_QUIET_AFTER_INTERVALS,
    TASK_WATCHER_STALE_AFTER_INTERVALS,
)
from ...eventbus import EventBus
from ...models import TasksSnapshot
from ...servicios.tareas.presencia import WatcherPresence
from ...servicios.tareas.tasks import TASKS_CHANGED, TASKS_PANEL_REQUESTED, TasksService
from ...servicios.tareas.vigilancia.eventos import (
    KIND_HEARTBEAT,
    TASK_WATCHER_AI_REMINDER,
    TASK_WATCHER_HEARTBEAT,
    TASK_WATCHER_REMINDER,
)
from ...ui import ShellModule
from ...ui.islands import IslandController, OrganicIslandHost
from ..tareas.panel import TasksPanel
from ..tareas.tasks_icon import TasksPulseIcon

_PRESENCE_CLASSES = (
    "tasks-watcher-unknown",
    "tasks-watcher-alive",
    "tasks-watcher-quiet",
    "tasks-watcher-inactive",
)

TASKS_ISLAND_ID = "tasks"


class TasksWidget(ShellModule):
    """Compact bar chrome for the personal task list."""

    def __init__(
        self,
        event_bus: EventBus,
        tasks_service: TasksService,
        shell_window: Gtk.Window,
        *,
        island_controller: IslandController | None = None,
    ) -> None:
        super().__init__("tasks-widget", spacing=0)
        self._event_bus = event_bus
        self._service = tasks_service
        self._shell_window = shell_window
        self._island_controller = island_controller
        self._island: OrganicIslandHost | None = None
        self._compact = False
        self._presence = WatcherPresence()
        self._stale_quiet_id = 0
        self._stale_inactive_id = 0

        self._overlay = Gtk.Overlay()
        self._button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._button.get_style_context().add_class("tasks-button")
        self._button.get_style_context().add_class("tasks-watcher-unknown")
        self._button.set_tooltip_text("Tareas")
        self._icon = TasksPulseIcon(TASKS_ICON_SIZE)
        self._button.add(self._icon)
        self._button.connect("clicked", self._on_button_clicked)

        self._badge = Gtk.Label(label="0")
        self._badge.get_style_context().add_class("tasks-badge")
        self._badge.set_halign(Gtk.Align.END)
        self._badge.set_valign(Gtk.Align.START)
        self._badge.set_margin_end(2)
        self._badge.set_no_show_all(True)
        self._badge.hide()

        self._overlay.add(self._button)
        self._overlay.add_overlay(self._badge)
        self.pack_start(self._overlay, False, False, 0)

        self._panel = TasksPanel(tasks_service)
        self._event_bus.subscribe(TASKS_CHANGED, self._on_tasks_changed)
        self._event_bus.subscribe(TASKS_PANEL_REQUESTED, self._on_panel_requested)
        self._event_bus.subscribe(TASK_WATCHER_HEARTBEAT, self._on_watcher_pulse)
        self._event_bus.subscribe(TASK_WATCHER_REMINDER, self._on_watcher_pulse)
        self._event_bus.subscribe(TASK_WATCHER_AI_REMINDER, self._on_watcher_pulse)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._sync_badge)

    def bind_island(self, island: OrganicIslandHost) -> None:
        self._island = island

    @property
    def panel(self) -> TasksPanel:
        return self._panel

    @property
    def last_heartbeat_at(self) -> float | None:
        return self._presence.last_heartbeat_at

    @property
    def watcher_status(self) -> str:
        return self._presence.status

    def apply_shell_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._icon.set_pixel_size(
            TASKS_COMPACT_ICON_SIZE if compact else TASKS_ICON_SIZE
        )

    def get_anchor_button(self) -> Gtk.Widget:
        return self._button

    def is_panel_open(self) -> bool:
        return self._island is not None and self._island.is_open

    def open_panel(self) -> None:
        if self._island_controller is not None:
            self._island_controller.open(TASKS_ISLAND_ID)
            return
        if self._island is not None:
            self._island.open()

    def close_panel(self) -> None:
        if self._island_controller is not None:
            self._island_controller.close(TASKS_ISLAND_ID)
            return
        if self._island is not None:
            self._island.close()

    def toggle_panel(self) -> None:
        if self.is_panel_open():
            self.close_panel()
            return
        self.open_panel()

    def close_popup(self) -> None:
        """Compatibility alias for shell compact / destroy paths."""
        self.close_panel()

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(TASKS_CHANGED, self._on_tasks_changed)
        self._event_bus.unsubscribe(TASKS_PANEL_REQUESTED, self._on_panel_requested)
        self._event_bus.unsubscribe(TASK_WATCHER_HEARTBEAT, self._on_watcher_pulse)
        self._event_bus.unsubscribe(TASK_WATCHER_REMINDER, self._on_watcher_pulse)
        self._event_bus.unsubscribe(TASK_WATCHER_AI_REMINDER, self._on_watcher_pulse)
        self._clear_stale_timers()
        self._icon.stop()
        self.close_panel()

    def _on_tasks_changed(self, _snapshot: TasksSnapshot) -> None:
        GLib.idle_add(self._handle_tasks_changed)

    def _on_panel_requested(self, _payload: object) -> None:
        GLib.idle_add(self._handle_panel_requested)

    def _on_watcher_pulse(self, kind: object) -> None:
        pulse = kind if isinstance(kind, str) else KIND_HEARTBEAT
        self._presence.note(pulse, time.time())
        self._sync_presence_style()
        self._icon.pulse(self._presence.last_kind)
        self._arm_stale_timers()

    def _handle_panel_requested(self) -> bool:
        if not self.is_panel_open():
            self.open_panel()
        return False

    def _handle_tasks_changed(self) -> bool:
        self._sync_badge()
        if self._island is not None and self._island.is_open:
            self._panel.refresh()
        return False

    def _sync_badge(self) -> bool:
        count = self._service.snapshot.pending_today_count
        if count <= 0:
            self._badge.hide()
        else:
            self._badge.set_text(str(count) if count <= 99 else "99+")
            self._badge.show()
        return False

    def _sync_presence_style(self) -> None:
        style = self._button.get_style_context()
        wanted = f"tasks-watcher-{self._presence.status}"
        for name in _PRESENCE_CLASSES:
            if name == wanted:
                style.add_class(name)
            else:
                style.remove_class(name)
        self._icon.set_presence(self._presence.status)

    def _arm_stale_timers(self) -> None:
        self._clear_stale_timers()
        interval = max(15, int(TASK_WATCHER_POLL_INTERVAL_SEC))
        quiet_sec = interval * max(1, int(TASK_WATCHER_QUIET_AFTER_INTERVALS))
        stale_sec = interval * max(1, int(TASK_WATCHER_STALE_AFTER_INTERVALS))
        self._stale_quiet_id = GLib.timeout_add_seconds(quiet_sec, self._on_watcher_quiet)
        self._stale_inactive_id = GLib.timeout_add_seconds(stale_sec, self._on_watcher_inactive)

    def _clear_stale_timers(self) -> None:
        if self._stale_quiet_id:
            GLib.source_remove(self._stale_quiet_id)
            self._stale_quiet_id = 0
        if self._stale_inactive_id:
            GLib.source_remove(self._stale_inactive_id)
            self._stale_inactive_id = 0

    def _on_watcher_quiet(self) -> bool:
        self._stale_quiet_id = 0
        if self._presence.mark_quiet():
            self._sync_presence_style()
        return False

    def _on_watcher_inactive(self) -> bool:
        self._stale_inactive_id = 0
        if self._presence.mark_inactive():
            self._sync_presence_style()
        return False

    def _on_button_clicked(self, *_args) -> None:
        self.toggle_panel()


def build_tasks_island(
    *,
    tasks_widget: TasksWidget,
    island_controller: IslandController,
    on_surface_expand,
    on_surface_restore,
) -> OrganicIslandHost:
    """Assemble the Organic Island host around the tasks chrome + panel."""
    host = OrganicIslandHost(
        TASKS_ISLAND_ID,
        compact=tasks_widget,
        expanded=tasks_widget.panel,
        stage=island_controller.stage,
        on_surface_expand=on_surface_expand,
        on_surface_restore=on_surface_restore,
        expanded_width=TASKS_POPUP_WIDTH,
        expanded_height=min(420, TASKS_POPUP_MAX_HEIGHT + 72),
        on_closed=lambda: island_controller.note_closed(TASKS_ISLAND_ID),
    )
    tasks_widget.bind_island(host)
    island_controller.register(host)
    return host
