"""One task row: checkbox, title, expandable notes, delete."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, Pango

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
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("task-row")
        if snapshot.status == TASK_STATUS_COMPLETED:
            self.get_style_context().add_class("task-row-completed")
        if snapshot.status == "overdue":
            self.get_style_context().add_class("task-row-overdue")
        if snapshot.status == "missed":
            self.get_style_context().add_class("task-row-missed")

        self._snapshot = snapshot
        self._notes = snapshot.notes.strip()
        self._expanded = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        check = Gtk.CheckButton()
        check.set_can_focus(False)
        check.set_active(snapshot.status == TASK_STATUS_COMPLETED)
        check.set_sensitive(can_toggle and snapshot.status != "missed")
        check.connect("toggled", lambda _btn: on_toggle(snapshot.id))
        check.get_style_context().add_class("task-row-check")
        header.pack_start(check, False, False, 0)

        text_event = Gtk.EventBox()
        text_event.set_visible_window(False)
        text_event.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        text_event.set_hexpand(True)
        if self._notes:
            text_event.set_tooltip_text("Ver nota")
            text_event.connect("button-press-event", self._on_body_clicked)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
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
        text_event.add(text)
        header.pack_start(text_event, True, True, 0)

        if self._notes:
            self._chevron = Gtk.Image.new_from_icon_name(
                "pan-end-symbolic",
                Gtk.IconSize.MENU,
            )
            self._chevron.get_style_context().add_class("task-row-chevron")
            chevron_btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            chevron_btn.get_style_context().add_class("task-row-chevron-button")
            chevron_btn.set_tooltip_text("Ver nota")
            chevron_btn.set_can_focus(False)
            chevron_btn.add(self._chevron)
            chevron_btn.connect(
                "clicked",
                lambda *_args: self._set_expanded(not self._expanded),
            )
            header.pack_start(chevron_btn, False, False, 0)
        else:
            self._chevron = None

        if on_delete is not None:
            delete = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            delete.set_tooltip_text("Eliminar")
            delete.get_style_context().add_class("task-row-delete")
            icon = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            delete.add(icon)
            delete.connect("clicked", lambda _btn: on_delete(snapshot.id))
            header.pack_start(delete, False, False, 0)

        self.pack_start(header, False, False, 0)

        self._notes_label = Gtk.Label(label=self._notes, xalign=0)
        self._notes_label.get_style_context().add_class("task-row-notes")
        self._notes_label.set_line_wrap(True)
        self._notes_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._notes_label.set_selectable(True)
        self._notes_label.set_no_show_all(True)
        self._notes_label.hide()
        self.pack_start(self._notes_label, False, False, 0)

    def _on_body_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1 or not self._notes:
            return False
        self._set_expanded(not self._expanded)
        return True

    def _set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        if expanded:
            self._notes_label.show()
            if self._chevron is not None:
                self._chevron.set_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        else:
            self._notes_label.hide()
            if self._chevron is not None:
                self._chevron.set_from_icon_name("pan-end-symbolic", Gtk.IconSize.MENU)
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            toplevel.queue_resize()
