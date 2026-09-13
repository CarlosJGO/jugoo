"""Anchored tasks panel window (legacy path; bar uses Organic Island)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import TASKS_POPUP_OFFSET, TASKS_POPUP_WIDTH
from ...popup_handle import hide_popup, pointer_inside_widget, present_popup
from ...popup_spawn import publish_popup_spawn
from ...servicios.tareas.tasks import TasksService
from ...ui.starfield import install_starfield, resolve_event_bus
from ...window_identity import (
    TITLE_TASKS,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from .panel import TasksPanel


class TasksPopup(Gtk.Window):
    """Interactive task list anchored below the bar button."""

    def __init__(self, shell_window: Gtk.Window, tasks_service: TasksService) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._shell_window = shell_window
        self._service = tasks_service
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._last_height = 0

        self.set_name("shell-tasks")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_TASKS)
        configure_interactive_popup(self)
        self.set_default_size(TASKS_POPUP_WIDTH, -1)

        self._panel = TasksPanel(
            tasks_service,
            on_layout_changed=self._reposition,
        )
        install_starfield(
            self,
            self._panel,
            resolve_event_bus(shell_window),
            corner_radius=16.0,
        )
        self.connect("size-allocate", self._on_size_allocate)

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._last_height = 0
        self._panel.hide_composer()
        self.refresh()
        publish_popup_spawn(
            self,
            anchor_button,
            title=TITLE_TASKS,
            offset=TASKS_POPUP_OFFSET,
        )
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor_button = None
        self._fixed_popup_top = None
        self._last_height = 0
        self._panel.hide_composer()
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self) -> None:
        self._panel.refresh()

    def _on_size_allocate(self, _widget: Gtk.Widget, allocation: Gtk.Allocation) -> None:
        height = int(allocation.height)
        if height <= 1 or height == self._last_height:
            return
        self._last_height = height
        self._reposition()

    def _reposition(self) -> None:
        if self.get_visible() and self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._anchor_button is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor_button,
                title=TITLE_TASKS,
                offset=TASKS_POPUP_OFFSET,
                fixed_top=self._fixed_popup_top,
            )
            if self._fixed_popup_top is None and top is not None:
                self._fixed_popup_top = top
        return False
