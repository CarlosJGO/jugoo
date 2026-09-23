"""Anchored tasks panel with Hoy / Completadas / Por fecha views."""

from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import TASKS_POPUP_MAX_HEIGHT, TASKS_POPUP_OFFSET, TASKS_POPUP_WIDTH
from ...models import TASK_STATUS_COMPLETED, TASK_STATUS_MISSED, TaskSnapshot
from ...popup_handle import hide_popup, pointer_inside_widget, present_popup
from ...popup_spawn import publish_popup_spawn
from ...servicios.tareas.logic import format_day_label, parse_iso_date
from ...servicios.tareas.tasks import TasksService
from ...settings.task_taxonomy import (
    category_label_map,
    parse_categories,
    parse_priorities,
    priority_label_map,
    priority_weight_map,
    sort_by_priority,
)
from ...ui.date_entry import DateEntry
from ...ui.starfield import install_starfield, resolve_event_bus
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

_TAB_TODAY = "hoy"
_TAB_DONE = "completadas"
_TAB_BY_DATE = "por_fecha"


class TasksPopup(Gtk.Window):
    """Interactive task list anchored below the bar button."""

    def __init__(self, shell_window: Gtk.Window, tasks_service: TasksService) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._shell_window = shell_window
        self._service = tasks_service
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._last_height = 0
        self._active_tab = _TAB_TODAY
        self._browse_date = date.today()
        self._category_labels: dict[str, str] = {}
        self._priority_labels: dict[str, str] = {}
        self._priority_weights: dict[str, int] = {}

        self.set_name("shell-tasks")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_TASKS)
        configure_interactive_popup(self)
        self.set_default_size(TASKS_POPUP_WIDTH, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("tasks-popup-content")
        outer.set_size_request(TASKS_POPUP_WIDTH, -1)
        install_starfield(
            self,
            outer,
            resolve_event_bus(shell_window),
        )

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

        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tabs.get_style_context().add_class("tasks-popup-tabs")
        self._tab_buttons: dict[str, Gtk.ToggleButton] = {}
        for key, label in (
            (_TAB_TODAY, "Hoy"),
            (_TAB_DONE, "Completadas"),
            (_TAB_BY_DATE, "Por fecha"),
        ):
            button = Gtk.ToggleButton(label=label)
            button.get_style_context().add_class("tasks-popup-tab")
            button.connect("toggled", self._on_tab_toggled, key)
            tabs.pack_start(button, True, True, 0)
            self._tab_buttons[key] = button
        self._tab_buttons[_TAB_TODAY].set_active(True)
        outer.pack_start(tabs, False, False, 0)

        self._date_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._date_bar.get_style_context().add_class("tasks-popup-date-bar")
        self._date_entry = DateEntry(initial=self._browse_date)
        self._date_entry.set_width_chars(12)
        self._date_entry.get_entry().connect("activate", self._on_date_apply)
        self._date_entry.connect_changed(lambda _value: self._on_date_apply())
        today_btn = Gtk.Button(label="Hoy", relief=Gtk.ReliefStyle.NONE)
        today_btn.get_style_context().add_class("tasks-popup-date-chip")
        today_btn.connect("clicked", self._on_date_today)
        apply_btn = Gtk.Button(label="Ver", relief=Gtk.ReliefStyle.NONE)
        apply_btn.get_style_context().add_class("tasks-popup-date-chip")
        apply_btn.connect("clicked", self._on_date_apply)
        self._date_bar.pack_start(self._date_entry, True, True, 0)
        self._date_bar.pack_start(today_btn, False, False, 0)
        self._date_bar.pack_start(apply_btn, False, False, 0)
        self._date_bar.set_no_show_all(True)
        self._date_bar.hide()
        outer.pack_start(self._date_bar, False, False, 0)

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
        self._reload_taxonomy()

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._last_height = 0
        self._hide_composer()
        self._reload_taxonomy()
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
        self._hide_composer()
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self) -> None:
        for child in self._list.get_children():
            self._list.remove(child)

        self._reload_taxonomy()
        show_date = self._active_tab == _TAB_BY_DATE
        self._date_bar.set_no_show_all(not show_date)
        if show_date:
            self._date_bar.show_all()
        else:
            self._date_bar.hide()

        if self._active_tab == _TAB_DONE:
            self._fill_completed()
        elif self._active_tab == _TAB_BY_DATE:
            self._fill_by_date()
        else:
            self._fill_today()
        self._list.show_all()
        self.queue_resize()
        self._reposition()

    def _fill_today(self) -> None:
        today = date.today()
        board = list(self._service.snapshot.tasks)
        upcoming = list(self._service.upcoming())
        if not self._service.records():
            self._list.pack_start(self._empty, False, False, 0)
            self._empty.set_text("No hay tareas todavía")
            self._empty.show()
            return

        overdue = sort_by_priority(
            [item for item in board if item.status == "overdue"],
            self._priority_weights,
        )
        today_items = [item for item in board if item.status != "overdue"]
        done_today = [item for item in today_items if item.status == TASK_STATUS_COMPLETED]
        open_today = sort_by_priority(
            [item for item in today_items if item.status != TASK_STATUS_COMPLETED],
            self._priority_weights,
        )
        later = sort_by_priority(
            [item for item in upcoming if item.status != TASK_STATUS_COMPLETED],
            self._priority_weights,
        )

        if overdue:
            self._pack_section("Vencidas", overdue)
        self._pack_section(
            f"Hoy · {format_day_label(today)}",
            open_today + done_today,
            empty_if_missing=True,
        )
        if later:
            self._pack_section("Próximas", later)

    def _fill_completed(self) -> None:
        items = list(self._service.completed())
        if not items:
            empty = Gtk.Label(label="Nada completado todavía", xalign=0)
            empty.get_style_context().add_class("tasks-popup-section-empty")
            self._list.pack_start(empty, False, False, 0)
            return
        self._pack_section("Completadas", items)

    def _fill_by_date(self) -> None:
        today = date.today()
        items = sort_by_priority(
            list(self._service.tasks_for_date(self._browse_date)),
            self._priority_weights,
        )
        heading = f"{format_day_label(self._browse_date)}"
        if self._browse_date == today:
            heading = f"Hoy · {heading}"
        if not items:
            self._list.pack_start(self._section_label(heading), False, False, 0)
            empty = Gtk.Label(label="Sin tareas este día", xalign=0)
            empty.get_style_context().add_class("tasks-popup-section-empty")
            self._list.pack_start(empty, False, False, 0)
            return
        self._pack_section(heading, items)

    def _pack_section(
        self,
        heading: str,
        items: list[TaskSnapshot],
        *,
        empty_if_missing: bool = False,
    ) -> None:
        if not items:
            if empty_if_missing:
                empty = Gtk.Label(label="Nada pendiente hoy", xalign=0)
                empty.get_style_context().add_class("tasks-popup-section-empty")
                self._list.pack_start(self._section_label(heading), False, False, 0)
                self._list.pack_start(empty, False, False, 0)
            return
        self._list.pack_start(self._section_label(heading), False, False, 0)
        for snapshot in items:
            row = TaskRow(
                snapshot,
                on_toggle=self._make_toggle(snapshot),
                on_delete=self._service.delete,
                on_edit=self._on_edit,
                can_toggle=snapshot.status != TASK_STATUS_MISSED,
                category_label=self._category_labels.get(snapshot.category_id or ""),
                priority_label=self._priority_labels.get(snapshot.priority_id or ""),
            )
            self._list.pack_start(row, False, False, 0)

    def _make_toggle(self, snapshot: TaskSnapshot):
        when = parse_iso_date(snapshot.occurrence_date) or date.today()

        def _toggle(_task_id: str) -> None:
            self._service.toggle(snapshot.id, on_date=when)

        return _toggle

    @staticmethod
    def _section_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("tasks-popup-section")
        return label

    def _reload_taxonomy(self) -> None:
        categories = ()
        priorities = ()
        manager = getattr(self._shell_window, "settings_manager", None)
        if manager is not None:
            categories = parse_categories(str(manager.get("widgets.task_categories_json") or ""))
            priorities = parse_priorities(str(manager.get("widgets.task_priorities_json") or ""))
        else:
            categories = parse_categories(None)
            priorities = parse_priorities(None)
        self._category_labels = category_label_map(categories)
        self._priority_labels = priority_label_map(priorities)
        self._priority_weights = priority_weight_map(priorities)
        self._composer.set_taxonomy(categories, priorities)

    def _on_tab_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active():
            if self._active_tab == key:
                button.handler_block_by_func(self._on_tab_toggled)
                button.set_active(True)
                button.handler_unblock_by_func(self._on_tab_toggled)
            return
        self._active_tab = key
        for other_key, other in self._tab_buttons.items():
            if other_key == key:
                continue
            other.handler_block_by_func(self._on_tab_toggled)
            other.set_active(False)
            other.handler_unblock_by_func(self._on_tab_toggled)
        self.refresh()

    def _on_date_today(self, *_args) -> None:
        self._browse_date = date.today()
        self._date_entry.set_date(self._browse_date)
        self.refresh()

    def _on_date_apply(self, *_args) -> None:
        parsed = self._date_entry.get_date()
        if parsed is None:
            self._date_entry.set_date(self._browse_date)
            return
        self._browse_date = parsed
        self._date_entry.set_date(parsed)
        self.refresh()

    def _on_toggle_composer(self, *_args) -> None:
        if self._composer.get_visible():
            self._hide_composer()
            return
        self._reload_taxonomy()
        self._composer.reveal()
        self.queue_resize()
        self._reposition()

    def _hide_composer(self) -> None:
        self._composer.hide()
        self._composer.set_no_show_all(True)
        self.queue_resize()
        self._reposition()

    def _on_composer_submit(self, payload: dict) -> None:
        task_id = payload.get("id")
        common = {
            "notes": payload.get("notes", ""),
            "repeat": payload.get("repeat", "none"),
            "due_date": payload.get("due_date"),
            "month_day": payload.get("month_day", 1),
            "category_id": payload.get("category_id"),
            "priority_id": payload.get("priority_id"),
        }
        if task_id:
            self._service.update_task(task_id, payload["title"], **common)
        else:
            self._service.add_task(payload["title"], **common)
        self._hide_composer()

    def _on_edit(self, snapshot: TaskSnapshot) -> None:
        record = next((item for item in self._service.records() if item.id == snapshot.id), None)
        if record is None:
            return
        self._reload_taxonomy()
        self._composer.edit(
            record.id,
            title=record.title,
            notes=record.notes,
            repeat=record.repeat,
            due_date=record.due_date,
            month_day=record.month_day,
            category_id=record.category_id,
            priority_id=record.priority_id,
        )
        self.queue_resize()
        self._reposition()

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
