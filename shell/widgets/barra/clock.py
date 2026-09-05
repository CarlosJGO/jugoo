"""Clock module showing current time, a compact date, and calendar tasks."""

from __future__ import annotations

from datetime import date, datetime

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import CLOCK_DATE_FORMAT, CLOCK_TIME_FORMAT
from ...eventbus import EventBus
from ...popup_handle import PopupOutsideDismiss, hide_popup, present_popup
from ...servicios.tareas.logic import format_day_label
from ...servicios.tareas.tasks import TASKS_CHANGED, TasksService
from ...ui import SHELL_MODULE_STACK_SPACING, ShellModule, shell_label
from ...window_identity import (
    TITLE_CLOCK_CALENDAR,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from ..tareas.task_row import TaskRow


class ClockCalendarPopup(Gtk.Window):
    """Calendar popup with the tasks that fall on the selected day."""

    def __init__(
        self,
        anchor: Gtk.Widget,
        *,
        event_bus: EventBus | None = None,
        tasks_service: TasksService | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._anchor: Gtk.Widget | None = anchor
        self._fixed_top: int | None = None
        self._event_bus = event_bus
        self._tasks_service = tasks_service
        self._selected: date = date.today()
        self._refreshing_calendar = False

        self.set_name("shell-clock-calendar")
        parent = anchor.get_toplevel()
        if isinstance(parent, Gtk.Window):
            register_shell_popup(self, parent)
        configure_toplevel(self, title=TITLE_CLOCK_CALENDAR)
        configure_interactive_popup(self)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.get_style_context().add_class("clock-calendar-content")
        self.add(outer)

        self._calendar = Gtk.Calendar()
        self._calendar.set_display_options(
            Gtk.CalendarDisplayOptions.SHOW_HEADING
            | Gtk.CalendarDisplayOptions.SHOW_DAY_NAMES
        )
        self._calendar.get_style_context().add_class("clock-calendar")
        self._calendar.connect("day-selected", self._on_day_selected)
        self._calendar.connect("month-changed", self._on_month_changed)
        outer.pack_start(self._calendar, False, False, 0)

        tasks_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tasks_header.get_style_context().add_class("clock-calendar-tasks-header")
        self._tasks_heading = Gtk.Label(label="Tareas", xalign=0)
        self._tasks_heading.get_style_context().add_class("clock-calendar-tasks-title")
        self._tasks_heading.set_hexpand(True)
        tasks_header.pack_start(self._tasks_heading, True, True, 0)
        if tasks_service is not None:
            open_all = Gtk.Button(label="Ver todas", relief=Gtk.ReliefStyle.NONE)
            open_all.get_style_context().add_class("clock-calendar-open-tasks")
            open_all.connect("clicked", self._on_open_all)
            tasks_header.pack_start(open_all, False, False, 0)
        outer.pack_start(tasks_header, False, False, 0)

        self._tasks_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._tasks_list.get_style_context().add_class("clock-calendar-tasks")
        outer.pack_start(self._tasks_list, False, False, 0)

        self._empty = Gtk.Label(label="Sin tareas este día", xalign=0)
        self._empty.get_style_context().add_class("clock-calendar-empty")

        if event_bus is not None and tasks_service is not None:
            event_bus.subscribe(TASKS_CHANGED, self._on_tasks_changed)
        self.connect("destroy", self._on_destroy)

    def open_for(self, anchor: Gtk.Widget) -> None:
        self._anchor = anchor
        self._fixed_top = None
        self._select_today()
        self._refresh_marks()
        self._refresh_day_tasks()
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close(self) -> None:
        hide_popup(self)

    def _on_destroy(self, *_args) -> None:
        if self._event_bus is not None:
            self._event_bus.unsubscribe(TASKS_CHANGED, self._on_tasks_changed)

    def _on_open_all(self, *_args) -> None:
        if self._tasks_service is not None:
            self._tasks_service.request_panel()

    def _on_tasks_changed(self, _snapshot: object) -> None:
        if self.get_visible():
            GLib.idle_add(self._refresh_from_service)

    def _refresh_from_service(self) -> bool:
        self._refresh_marks()
        self._refresh_day_tasks()
        return False

    def _select_today(self) -> None:
        today = date.today()
        self._refreshing_calendar = True
        self._calendar.select_month(today.month - 1, today.year)
        self._calendar.select_day(today.day)
        self._selected = today
        self._refreshing_calendar = False

    def _on_day_selected(self, calendar: Gtk.Calendar) -> None:
        if self._refreshing_calendar:
            return
        year, month, day = calendar.get_date()
        self._selected = date(int(year), int(month) + 1, int(day))
        self._refresh_day_tasks()

    def _on_month_changed(self, _calendar: Gtk.Calendar) -> None:
        if self._refreshing_calendar:
            return
        self._refresh_marks()
        self._on_day_selected(self._calendar)

    def _refresh_marks(self) -> None:
        self._calendar.clear_marks()
        if self._tasks_service is None:
            return
        year, month, _day = self._calendar.get_date()
        for day in self._tasks_service.marked_days(int(year), int(month) + 1):
            self._calendar.mark_day(day)

    def _refresh_day_tasks(self) -> None:
        for child in self._tasks_list.get_children():
            self._tasks_list.remove(child)
        self._tasks_heading.set_text(f"Tareas · {format_day_label(self._selected)}")
        if self._tasks_service is None:
            self._tasks_list.pack_start(self._empty, False, False, 0)
            self._empty.set_text("Sin módulo de tareas")
            self._tasks_list.show_all()
            return

        today = date.today()
        items = list(self._tasks_service.tasks_for_date(self._selected))
        if self._selected == today:
            seen = {item.id for item in items}
            for item in self._tasks_service.snapshot.tasks:
                if item.status == "overdue" and item.id not in seen:
                    items.append(item)
                    seen.add(item.id)
        if not items:
            hint = (
                "Sin tareas este día"
                if self._selected == today
                else "Sin vencimientos este día"
            )
            self._empty.set_text(hint)
            self._tasks_list.pack_start(self._empty, False, False, 0)
            self._tasks_list.show_all()
            return

        can_toggle = self._selected == today
        for snapshot in items:
            row = TaskRow(
                snapshot,
                on_toggle=self._tasks_service.toggle,
                compact=True,
                can_toggle=can_toggle,
            )
            self._tasks_list.pack_start(row, False, False, 0)
        self._tasks_list.show_all()

    def _position_after_show(self) -> bool:
        if self._anchor is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor,
                title=TITLE_CLOCK_CALENDAR,
                offset=8,
                fixed_top=self._fixed_top,
            )
            if self._fixed_top is None and top is not None:
                self._fixed_top = top
        return False


class ClockWidget(ShellModule):
    """Self-contained clock that ticks on GTK's main loop."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        tasks_service: TasksService | None = None,
    ) -> None:
        super().__init__(
            "clock-widget",
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SHELL_MODULE_STACK_SPACING,
        )
        self._event_bus = event_bus
        self._tasks_service = tasks_service
        self._tick_source_id: int | None = None
        self._calendar_popup: ClockCalendarPopup | None = None
        self._outside_dismiss = PopupOutsideDismiss()

        self._content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SHELL_MODULE_STACK_SPACING,
        )
        self._hover_surface = Gtk.EventBox()
        self._hover_surface.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._hover_surface.connect("enter-notify-event", self._on_pointer_enter)

        self._time_label = shell_label(
            "",
            role="title",
            css_classes=("clock-time",),
            xalign=1.0,
        )
        self._date_label = shell_label(
            "",
            role="caption",
            css_classes=("clock-date",),
            xalign=1.0,
        )

        self._content_box.pack_start(self._time_label, False, False, 0)
        self._content_box.pack_start(self._date_label, False, False, 0)
        self._hover_surface.add(self._content_box)
        self.pack_start(self._hover_surface, False, False, 0)

        self.connect("destroy", self._on_destroy)
        self._refresh_display()
        self._schedule_next_tick()

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.strftime(CLOCK_TIME_FORMAT)

    def apply_shell_compact(self, compact: bool) -> None:
        return

    def _refresh_display(self) -> None:
        now = datetime.now()
        self._time_label.set_text(self._format_time(now))
        self._date_label.set_text(now.strftime(CLOCK_DATE_FORMAT))

    def _schedule_next_tick(self) -> None:
        now = datetime.now()
        seconds_until_next_minute = 60 - now.second
        if now.microsecond:
            seconds_until_next_minute = max(1, seconds_until_next_minute)
        self._arm_tick(seconds_until_next_minute)

    def _arm_tick(self, interval_sec: int) -> None:
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
        self._tick_source_id = GLib.timeout_add_seconds(
            interval_sec,
            self._on_tick,
        )

    def _on_tick(self) -> bool:
        self._refresh_display()
        self._arm_tick(60)
        return False

    def _ensure_calendar_popup(self) -> ClockCalendarPopup:
        if self._calendar_popup is None:
            self._calendar_popup = ClockCalendarPopup(
                self,
                event_bus=self._event_bus,
                tasks_service=self._tasks_service,
            )
        return self._calendar_popup

    def _close_calendar(self) -> None:
        if self._calendar_popup is not None:
            self._calendar_popup.close()

    def _open_calendar(self) -> None:
        popup = self._ensure_calendar_popup()
        if popup.get_visible():
            return
        popup.open_for(self)
        shell = self.get_toplevel()
        if not isinstance(shell, Gtk.Window):
            return
        self._outside_dismiss.install(
            popup,
            shell,
            (self._hover_surface,),
            self._close_calendar,
            self._event_bus,
        )

    def _on_pointer_enter(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        mode = getattr(event, "mode", None)
        if mode in (Gdk.CrossingMode.GRAB, Gdk.CrossingMode.UNGRAB):
            return False
        self._open_calendar()
        return False

    def _on_destroy(self, *_args) -> None:
        self._outside_dismiss.uninstall()
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
        if self._calendar_popup is not None:
            self._calendar_popup.destroy()
            self._calendar_popup = None
