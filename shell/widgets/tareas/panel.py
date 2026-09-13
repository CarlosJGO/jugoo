"""Reusable tasks list panel (shared by Organic Island and legacy popup)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import TASKS_POPUP_MAX_HEIGHT, TASKS_POPUP_WIDTH
from ...models import TASK_STATUS_COMPLETED, TaskSnapshot
from ...servicios.tareas.logic import format_day_label
from ...servicios.tareas.tasks import TasksService
from .composer import TaskComposer
from .task_row import TaskRow


class TasksPanel(Gtk.Box):
    """Today / overdue / upcoming task list with composer."""

    def __init__(
        self,
        tasks_service: TasksService,
        *,
        on_layout_changed: Callable[[], None] | None = None,
        max_height: int | None = None,
        width: int | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("tasks-popup-content")
        self._service = tasks_service
        self._on_layout_changed = on_layout_changed
        panel_width = TASKS_POPUP_WIDTH if width is None else int(width)
        panel_max = TASKS_POPUP_MAX_HEIGHT if max_height is None else int(max_height)
        self.set_size_request(panel_width, -1)

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
        self.pack_start(header, False, False, 0)

        self._composer = TaskComposer(
            on_submit=self._on_composer_submit,
            on_cancel=self._hide_composer,
        )
        self._composer.set_no_show_all(True)
        self._composer.hide()
        self.pack_start(self._composer, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(panel_max)
        scrolled.get_style_context().add_class("tasks-popup-scroll")
        self.pack_start(scrolled, True, True, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._list.get_style_context().add_class("tasks-popup-list")
        scrolled.add(self._list)

        self._empty = Gtk.Label(label="No hay tareas todavía")
        self._empty.get_style_context().add_class("tasks-popup-empty")
        self._empty.set_margin_top(12)
        self._empty.set_margin_bottom(12)

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
        today_items = [item for item in board if item.status != "overdue"]
        done_today = [item for item in today_items if item.status == TASK_STATUS_COMPLETED]
        open_today = [item for item in today_items if item.status != TASK_STATUS_COMPLETED]

        if overdue:
            self._pack_section("Vencidas", overdue)
        self._pack_section(f"Hoy · {format_day_label(today)}", open_today + done_today)
        later = [item for item in upcoming if item.status != TASK_STATUS_COMPLETED]
        if later:
            self._pack_section("Próximas", later)
        self._list.show_all()

    def hide_composer(self) -> None:
        self._hide_composer()

    def _notify_layout(self) -> None:
        if self._on_layout_changed is not None:
            self._on_layout_changed()

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
                on_edit=self._on_edit,
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
        self._notify_layout()

    def _hide_composer(self) -> None:
        self._composer.hide()
        self._composer.set_no_show_all(True)
        self.queue_resize()
        self._notify_layout()

    def _on_composer_submit(self, payload: dict) -> None:
        task_id = payload.get("id")
        if task_id:
            self._service.update_task(
                task_id,
                payload["title"],
                notes=payload.get("notes", ""),
                repeat=payload.get("repeat", "none"),
                due_date=payload.get("due_date"),
                month_day=payload.get("month_day", 1),
            )
        else:
            self._service.add_task(
                payload["title"],
                notes=payload.get("notes", ""),
                repeat=payload.get("repeat", "none"),
                due_date=payload.get("due_date"),
                month_day=payload.get("month_day", 1),
            )
        self._hide_composer()

    def _on_edit(self, snapshot: TaskSnapshot) -> None:
        record = next((item for item in self._service.records() if item.id == snapshot.id), None)
        if record is None:
            return
        self._composer.edit(
            record.id,
            title=record.title,
            notes=record.notes,
            repeat=record.repeat,
            due_date=record.due_date,
            month_day=record.month_day,
        )
        self.queue_resize()
        self._notify_layout()
