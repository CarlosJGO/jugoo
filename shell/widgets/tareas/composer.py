"""Inline form to create a one-shot, daily, or monthly task."""

from __future__ import annotations

from datetime import date
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...models import TASK_REPEAT_DAILY, TASK_REPEAT_MONTHLY, TASK_REPEAT_NONE
from ...settings.task_taxonomy import TaskCategory, TaskPriority
from ...ui.date_entry import DateEntry

_REPEAT_OPTIONS = (
    ("Una vez", TASK_REPEAT_NONE),
    ("Cada día", TASK_REPEAT_DAILY),
    ("Cada mes", TASK_REPEAT_MONTHLY),
)

_NONE_ID = ""


class TaskComposer(Gtk.Box):
    """Compact editor packed at the top of the tasks popup."""

    def __init__(
        self,
        *,
        on_submit: Callable[[dict], None],
        on_cancel: Callable[[], None] | None = None,
        categories: Sequence[TaskCategory] = (),
        priorities: Sequence[TaskPriority] = (),
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("task-composer")
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._repeat = TASK_REPEAT_NONE
        self._editing_id: str | None = None
        self._categories = tuple(categories)
        self._priorities = tuple(priorities)

        self._title = Gtk.Entry()
        self._title.set_placeholder_text("Nueva tarea")
        self._title.connect("activate", self._submit)
        self.pack_start(self._title, False, False, 0)

        self._notes = Gtk.Entry()
        self._notes.set_placeholder_text("Notas (opcional)")
        self.pack_start(self._notes, False, False, 0)

        taxonomy = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._category_combo = self._build_combo(
            (("Sin categoría", _NONE_ID),)
            + tuple((item.label, item.id) for item in self._categories)
        )
        self._priority_combo = self._build_combo(
            (("Sin prioridad", _NONE_ID),)
            + tuple((item.label, item.id) for item in self._priorities)
        )
        taxonomy.pack_start(self._category_combo, True, True, 0)
        taxonomy.pack_start(self._priority_combo, True, True, 0)
        self.pack_start(taxonomy, False, False, 0)

        repeat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        repeat_label = Gtk.Label(label="Repetir", xalign=0)
        repeat_label.get_style_context().add_class("task-composer-label")
        repeat_row.pack_start(repeat_label, False, False, 0)
        self._repeat_buttons: dict[str, Gtk.RadioButton] = {}
        group: Gtk.RadioButton | None = None
        for label, value in _REPEAT_OPTIONS:
            button = Gtk.RadioButton(label=label)
            if group is not None:
                button.join_group(group)
            else:
                group = button
            button.get_style_context().add_class("task-composer-repeat")
            button.connect("toggled", self._on_repeat_toggled, value)
            self._repeat_buttons[value] = button
            repeat_row.pack_start(button, False, False, 0)
        self.pack_start(repeat_row, False, False, 0)

        self._once_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        due_label = Gtk.Label(label="Vence", xalign=0)
        due_label.get_style_context().add_class("task-composer-label")
        self._due_entry = DateEntry(initial=date.today())
        self._due_entry.set_width_chars(12)
        today_btn = Gtk.Button(label="Hoy", relief=Gtk.ReliefStyle.NONE)
        today_btn.get_style_context().add_class("task-composer-chip")
        today_btn.connect("clicked", lambda _btn: self._due_entry.set_date(date.today()))
        self._once_row.pack_start(due_label, False, False, 0)
        self._once_row.pack_start(self._due_entry, True, True, 0)
        self._once_row.pack_start(today_btn, False, False, 0)
        self.pack_start(self._once_row, False, False, 0)

        self._month_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        month_label = Gtk.Label(label="Día del mes", xalign=0)
        month_label.get_style_context().add_class("task-composer-label")
        adjustment = Gtk.Adjustment(
            value=date.today().day,
            lower=1,
            upper=31,
            step_increment=1,
            page_increment=5,
        )
        self._month_spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
        self._month_spin.set_numeric(True)
        self._month_row.pack_start(month_label, False, False, 0)
        self._month_row.pack_end(self._month_spin, False, False, 0)
        self.pack_start(self._month_row, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancelar", relief=Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("task-composer-cancel")
        cancel.connect("clicked", lambda _btn: self._cancel())
        save = Gtk.Button(label="Añadir")
        save.get_style_context().add_class("task-composer-save")
        save.connect("clicked", self._submit)
        actions.pack_start(cancel, False, False, 0)
        actions.pack_start(save, False, False, 0)
        self.pack_start(actions, False, False, 0)

        self._once_row.set_no_show_all(False)
        self._month_row.set_no_show_all(True)
        self._sync_repeat_rows()

    def set_taxonomy(
        self,
        categories: Sequence[TaskCategory],
        priorities: Sequence[TaskPriority],
    ) -> None:
        self._categories = tuple(categories)
        self._priorities = tuple(priorities)
        self._refill_combo(
            self._category_combo,
            (("Sin categoría", _NONE_ID),)
            + tuple((item.label, item.id) for item in self._categories),
        )
        self._refill_combo(
            self._priority_combo,
            (("Sin prioridad", _NONE_ID),)
            + tuple((item.label, item.id) for item in self._priorities),
        )

    def focus_title(self) -> None:
        self._title.grab_focus()

    def edit(
        self,
        task_id: str,
        *,
        title: str,
        notes: str,
        repeat: str,
        due_date: str | None,
        month_day: int,
        category_id: str | None = None,
        priority_id: str | None = None,
    ) -> None:
        self._editing_id = task_id
        self._title.set_text(title)
        self._notes.set_text(notes)
        self._repeat = repeat if repeat in self._repeat_buttons else TASK_REPEAT_NONE
        self._repeat_buttons[self._repeat].set_active(True)
        if due_date:
            self._due_entry.set_text(due_date)
        else:
            self._due_entry.set_date(date.today())
        self._month_spin.set_value(month_day)
        self._set_combo_id(self._category_combo, category_id or _NONE_ID)
        self._set_combo_id(self._priority_combo, priority_id or _NONE_ID)
        self.set_no_show_all(False)
        self.show_all()
        self._sync_repeat_rows()
        self.focus_title()

    def reveal(self) -> None:
        self.set_no_show_all(False)
        self.reset()
        self.show_all()
        self._sync_repeat_rows()
        self.focus_title()

    def reset(self) -> None:
        self._editing_id = None
        self._title.set_text("")
        self._notes.set_text("")
        self._repeat = TASK_REPEAT_NONE
        none_button = self._repeat_buttons[TASK_REPEAT_NONE]
        none_button.set_active(True)
        self._due_entry.set_date(date.today())
        self._month_spin.set_value(date.today().day)
        self._set_combo_id(self._category_combo, _NONE_ID)
        self._set_combo_id(self._priority_combo, _NONE_ID)
        self._sync_repeat_rows()

    @staticmethod
    def _build_combo(options: tuple[tuple[str, str], ...]) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        combo.get_style_context().add_class("task-composer-combo")
        for label, value in options:
            combo.append(value, label)
        combo.set_active(0)
        return combo

    @staticmethod
    def _refill_combo(combo: Gtk.ComboBoxText, options: tuple[tuple[str, str], ...]) -> None:
        current = combo.get_active_id() or _NONE_ID
        combo.remove_all()
        for label, value in options:
            combo.append(value, label)
        if not combo.set_active_id(current):
            combo.set_active(0)

    @staticmethod
    def _set_combo_id(combo: Gtk.ComboBoxText, ident: str) -> None:
        if not combo.set_active_id(ident):
            combo.set_active(0)

    @staticmethod
    def _combo_id(combo: Gtk.ComboBoxText) -> str:
        return combo.get_active_id() or _NONE_ID

    def _cancel(self) -> None:
        self.reset()
        if self._on_cancel is not None:
            self._on_cancel()

    def _on_repeat_toggled(self, button: Gtk.RadioButton, value: str) -> None:
        if not button.get_active():
            return
        self._repeat = value
        self._sync_repeat_rows()

    def _sync_repeat_rows(self) -> None:
        show_once = self._repeat == TASK_REPEAT_NONE
        show_month = self._repeat == TASK_REPEAT_MONTHLY
        self._once_row.set_no_show_all(not show_once)
        self._month_row.set_no_show_all(not show_month)
        if show_once:
            self._once_row.show_all()
        else:
            self._once_row.hide()
        if show_month:
            self._month_row.show_all()
        else:
            self._month_row.hide()

    def _submit(self, *_args) -> None:
        title = self._title.get_text().strip()
        if not title:
            self._title.grab_focus()
            return
        payload = {
            "id": self._editing_id,
            "title": title,
            "notes": self._notes.get_text().strip(),
            "repeat": self._repeat,
            "due_date": self._due_entry.get_text().strip() or None,
            "month_day": int(self._month_spin.get_value()),
            "category_id": self._combo_id(self._category_combo) or None,
            "priority_id": self._combo_id(self._priority_combo) or None,
        }
        self._on_submit(payload)
        self.reset()
