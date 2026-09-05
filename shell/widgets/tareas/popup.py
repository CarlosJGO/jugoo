"""Anchored tasks panel with today, overdue, recurring, and upcoming lists."""

from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import TASKS_POPUP_MAX_HEIGHT, TASKS_POPUP_OFFSET, TASKS_POPUP_WIDTH
from ...models import TASK_STATUS_COMPLETED, TaskSnapshot
from ...popup_handle import hide_popup, pointer_inside_widget, present_popup
from ...servicios.tareas.logic import format_day_label
from ...servicios.tareas.tasks import TasksService
from ...window_identity import (
    TITLE_TASKS,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from .composer import TaskComposer
from .task_row import TaskRow


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

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("tasks-popup-content")
        outer.set_size_request(TASKS_POPUP_WIDTH, -1)
        self.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("tasks-popup-header")
        title = Gtk.Label(label="Tareas", xalign=0)
        title.get_style_context().add_class("tasks-popup-title")
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)
        self._add_button = Gtk.Button(label="Nueva", relief=Gtk.ReliefStyle.NONE)
        self._add_button.get_style_context().add_class("tasks-popup-add")
        self._add_button.connect("clicked", self._on_toggle_composer)
        header.pack_start(self._add_button, False, False, 0)
        outer.pack_start(header, False, False, 0)

        self._composer = TaskComposer(
            on_submit=self._on_composer_submit,
            on_cancel=self._hide_composer,
        )
        self._composer.set_no_show_all(True)
        self._composer.hide()
        outer.pack_start(self._composer, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(TASKS_POPUP_MAX_HEIGHT)
        scrolled.get_style_context().add_class("tasks-popup-scroll")
        outer.pack_start(scrolled, True, True, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._list.get_style_context().add_class("tasks-popup-list")
        scrolled.add(self._list)

        self._empty = Gtk.Label(label="No hay tareas todavía")
        self._empty.get_style_context().add_class("tasks-popup-empty")
        self._empty.set_margin_top(12)
        self._empty.set_margin_bottom(12)
        self.connect("size-allocate", self._on_size_allocate)

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._last_height = 0
        self._hide_composer()
        self.refresh()
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor_button = None
        self._fixed_popup_top = None
        self._last_height = 0
        self._hide_composer()
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self) -> None:
        for child in self._list.get_children():
            self._list.remove(child)

        today = date.today()
        board = list(self._service.snapshot.tasks)
        upcoming = list(self._service.upcoming())
        records_empty = not self._service.records()

        if records_empty:
            self._list.pack_start(self._empty, False, False, 0)
            self._empty.show()
            self._list.show_all()
            return

        overdue = [item for item in board if item.status == "overdue"]
        today_items = [
            item
            for item in board
            if item.status != "overdue"
        ]
        done_today = [item for item in today_items if item.status == TASK_STATUS_COMPLETED]
        open_today = [item for item in today_items if item.status != TASK_STATUS_COMPLETED]

        if overdue:
            self._pack_section("Vencidas", overdue)
        self._pack_section(f"Hoy · {format_day_label(today)}", open_today + done_today)
        later = [item for item in upcoming if item.status != TASK_STATUS_COMPLETED]
        if later:
            self._pack_section("Próximas", later)
        self._list.show_all()

    def _pack_section(self, heading: str, items: list[TaskSnapshot]) -> None:
        if not items:
            if heading.startswith("Hoy"):
                empty = Gtk.Label(label="Nada pendiente hoy", xalign=0)
                empty.get_style_context().add_class("tasks-popup-section-empty")
                self._list.pack_start(self._section_label(heading), False, False, 0)
                self._list.pack_start(empty, False, False, 0)
            return
        self._list.pack_start(self._section_label(heading), False, False, 0)
        for snapshot in items:
            row = TaskRow(
                snapshot,
                on_toggle=self._service.toggle,
                on_delete=self._service.delete,
                can_toggle=snapshot.occurrence_date == date.today().isoformat(),
            )
            self._list.pack_start(row, False, False, 0)

    @staticmethod
    def _section_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("tasks-popup-section")
        return label

    def _on_toggle_composer(self, *_args) -> None:
        if self._composer.get_visible():
            self._hide_composer()
            return
        self._composer.reveal()
        self.queue_resize()
        self._reposition()

    def _hide_composer(self) -> None:
        self._composer.hide()
        self._composer.set_no_show_all(True)
        self.queue_resize()
        self._reposition()

    def _on_composer_submit(self, payload: dict) -> None:
        self._service.add_task(
            payload["title"],
            notes=payload.get("notes", ""),
            repeat=payload.get("repeat", "none"),
            due_date=payload.get("due_date"),
            month_day=payload.get("month_day", 1),
        )
        self._hide_composer()

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
