"""One task row: checkbox, title, recurrence/due meta, delete."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Pango

from ...models import TASK_STATUS_COMPLETED, TaskSnapshot
from ...servicios.tareas.logic import meta_label


class TaskRow(Gtk.Box):
    """Interactive row used by the tasks panel and the clock calendar."""

    def __init__(
        self,
        snapshot: TaskSnapshot,
        *,
        on_toggle: Callable[[str], None],
        on_delete: Callable[[str], None] | None = None,
        compact: bool = False,
        can_toggle: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.get_style_context().add_class("task-row")
        if snapshot.status == TASK_STATUS_COMPLETED:
            self.get_style_context().add_class("task-row-completed")
        if snapshot.status == "overdue":
            self.get_style_context().add_class("task-row-overdue")
        if snapshot.status == "missed":
            self.get_style_context().add_class("task-row-missed")

        self._snapshot = snapshot
        check = Gtk.CheckButton()
        check.set_can_focus(False)
        check.set_active(snapshot.status == TASK_STATUS_COMPLETED)
        check.set_sensitive(can_toggle and snapshot.status != "missed")
        check.connect("toggled", lambda _btn: on_toggle(snapshot.id))
        check.get_style_context().add_class("task-row-check")
        self.pack_start(check, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)
        title = Gtk.Label(label=snapshot.title, xalign=0)
        title.get_style_context().add_class("task-row-title")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_line_wrap(False)
        text.pack_start(title, False, False, 0)
        if not compact:
            meta = Gtk.Label(label=meta_label(snapshot), xalign=0)
            meta.get_style_context().add_class("task-row-meta")
            meta.set_ellipsize(Pango.EllipsizeMode.END)
            text.pack_start(meta, False, False, 0)
        self.pack_start(text, True, True, 0)

        if on_delete is not None:
            delete = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            delete.set_tooltip_text("Eliminar")
            delete.get_style_context().add_class("task-row-delete")
            icon = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            delete.add(icon)
            delete.connect("clicked", lambda _btn: on_delete(snapshot.id))
            self.pack_start(delete, False, False, 0)
