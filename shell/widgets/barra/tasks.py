"""Bar button that opens the tasks panel and shows today's pending count."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ...config import TASKS_COMPACT_ICON_SIZE, TASKS_ICON_SIZE
from ...eventbus import EventBus
from ...models import TasksSnapshot
from ...popup_handle import PopupHandle, PopupOutsideDismiss
from ...servicios.tareas.tasks import TASKS_CHANGED, TASKS_PANEL_REQUESTED, TasksService
from ...ui import ShellModule
from ..tareas.popup import TasksPopup


class TasksWidget(ShellModule):
    """Bar control for the personal task list."""

    def __init__(
        self,
        event_bus: EventBus,
        tasks_service: TasksService,
        shell_window: Gtk.Window,
    ) -> None:
        super().__init__("tasks-widget", spacing=0)
        self._event_bus = event_bus
        self._service = tasks_service
        self._shell_window = shell_window
        self._compact = False

        self._overlay = Gtk.Overlay()
        self._button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._button.get_style_context().add_class("tasks-button")
        self._button.set_tooltip_text("Tareas")
        self._icon = Gtk.Image.new_from_icon_name(
            "view-list-symbolic",
            Gtk.IconSize.BUTTON,
        )
        self._icon.set_pixel_size(TASKS_ICON_SIZE)
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

        self._popup = PopupHandle(self._create_popup)
        self._outside_click = PopupOutsideDismiss()
        self._event_bus.subscribe(TASKS_CHANGED, self._on_tasks_changed)
        self._event_bus.subscribe(TASKS_PANEL_REQUESTED, self._on_panel_requested)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._sync_badge)

    def apply_shell_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._icon.set_pixel_size(
            TASKS_COMPACT_ICON_SIZE if compact else TASKS_ICON_SIZE
        )

    def get_anchor_button(self) -> Gtk.Widget:
        return self._button

    def _create_popup(self) -> TasksPopup:
        return TasksPopup(self._shell_window, self._service)

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(TASKS_CHANGED, self._on_tasks_changed)
        self._event_bus.unsubscribe(TASKS_PANEL_REQUESTED, self._on_panel_requested)
        self.close_popup()

    def _on_tasks_changed(self, _snapshot: TasksSnapshot) -> None:
        GLib.idle_add(self._handle_tasks_changed)

    def _on_panel_requested(self, _payload: object) -> None:
        GLib.idle_add(self._handle_panel_requested)

    def _handle_panel_requested(self) -> bool:
        if not self._popup.is_visible():
            self._open_popup()
        return False

    def _handle_tasks_changed(self) -> bool:
        self._sync_badge()
        popup = self._popup.maybe
        if popup is not None and popup.get_visible():
            popup.refresh()
        return False

    def _sync_badge(self) -> bool:
        count = self._service.snapshot.pending_today_count
        if count <= 0:
            self._badge.hide()
        else:
            self._badge.set_text(str(count) if count <= 99 else "99+")
            self._badge.show()
        return False

    def _on_button_clicked(self, *_args) -> None:
        if self._popup.is_visible():
            self.close_popup()
            return
        self._open_popup()

    def _open_popup(self) -> None:
        popup = self._popup.get()
        popup.open_for(self._button)
        self._outside_click.install(
            popup,
            self._shell_window,
            (self._button,),
            self.close_popup,
            self._event_bus,
        )

    def close_popup(self) -> None:
        self._outside_click.uninstall()
        popup = self._popup.maybe
        if popup is not None:
            popup.close_popup()
