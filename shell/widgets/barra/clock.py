"""Clock module showing current time, a compact date, and calendar tasks."""

from __future__ import annotations

from calendar import Calendar, monthrange
from datetime import date, datetime

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ... import config as shell_config
from ...eventbus import EventBus
from ...settings.manager import SETTINGS_CHANGED
from ...popup_handle import PopupOutsideDismiss, hide_popup, present_popup
from ...popup_spawn import publish_popup_spawn
from ...servicios.tareas.logic import calendar_day_mark, format_day_label
from ...servicios.tareas.tasks import TASKS_CHANGED, TasksService
from ...ui.starfield import install_starfield, resolve_event_bus
from ...ui import SHELL_MODULE_STACK_SPACING, ShellModule, shell_label
from ...window_identity import (
    TITLE_CLOCK_CALENDAR,
    anchor_button_geometry,
    compute_popup_top_left,
    configure_interactive_popup,
    configure_toplevel,
    monitor_containing_point,
    popup_window_size,
    register_shell_popup,
    reposition_popup,
    schedule_popup_position,
)
from ..tareas.task_row import TaskRow

_MONTH_LABELS = (
    "",
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)
_WEEKDAY_LABELS = ("Lu", "Ma", "Mi", "Ju", "Vi", "Sa", "Do")
_MONTH_GRID = Calendar(firstweekday=0)


class TaskMonthGrid(Gtk.Box):
    """Compact month grid with visible dots on days that have tasks."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("clock-calendar-grid")
        self._year = date.today().year
        self._month = date.today().month
        self._selected = date.today()
        self._marks: dict[int, tuple[int, bool, tuple[str, ...]]] = {}
        self._day_buttons: dict[date, Gtk.Button] = {}
        self._on_day_selected_cb = None
        self._on_month_changed_cb = None

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        nav.get_style_context().add_class("clock-calendar-nav")
        prev = Gtk.Button(label="‹", relief=Gtk.ReliefStyle.NONE)
        prev.get_style_context().add_class("clock-calendar-nav-btn")
        prev.connect("clicked", self._on_prev_month)
        next_btn = Gtk.Button(label="›", relief=Gtk.ReliefStyle.NONE)
        next_btn.get_style_context().add_class("clock-calendar-nav-btn")
        next_btn.connect("clicked", self._on_next_month)
        self._heading = Gtk.Label(xalign=0.5)
        self._heading.get_style_context().add_class("clock-calendar-heading")
        self._heading.set_hexpand(True)
        nav.pack_start(prev, False, False, 0)
        nav.pack_start(self._heading, True, True, 0)
        nav.pack_start(next_btn, False, False, 0)
        self.pack_start(nav, False, False, 0)

        weekday_row = Gtk.Grid(column_spacing=2)
        weekday_row.get_style_context().add_class("clock-calendar-weekdays")
        for col, label in enumerate(_WEEKDAY_LABELS):
            cell = Gtk.Label(label=label)
            cell.get_style_context().add_class("clock-calendar-weekday")
            cell.set_hexpand(True)
            weekday_row.attach(cell, col, 0, 1, 1)
        self.pack_start(weekday_row, False, False, 0)

        self._grid = Gtk.Grid(row_spacing=2, column_spacing=2)
        self._grid.get_style_context().add_class("clock-calendar-days")
        self._grid.set_column_homogeneous(True)
        self.pack_start(self._grid, False, False, 0)
        self._rebuild_days()

    def connect_day_selected(self, callback) -> None:
        self._on_day_selected_cb = callback

    def connect_month_changed(self, callback) -> None:
        self._on_month_changed_cb = callback

    @property
    def visible_year(self) -> int:
        return self._year

    @property
    def visible_month(self) -> int:
        return self._month

    def set_marks(self, marks: dict[int, tuple[int, bool, tuple[str, ...]]]) -> None:
        self._marks = marks
        self._apply_marks()

    def select_date(self, value: date) -> None:
        self._year = value.year
        self._month = value.month
        self._selected = value
        self._rebuild_days()

    def _emit_month_changed(self) -> None:
        if self._on_month_changed_cb is not None:
            self._on_month_changed_cb(self)

    def _on_prev_month(self, *_args) -> None:
        if self._month == 1:
            self._year -= 1
            self._month = 12
        else:
            self._month -= 1
        self._emit_month_changed()
        self._rebuild_days()

    def _on_next_month(self, *_args) -> None:
        if self._month == 12:
            self._year += 1
            self._month = 1
        else:
            self._month += 1
        self._emit_month_changed()
        self._rebuild_days()

    def _rebuild_days(self) -> None:
        for child in self._grid.get_children():
            self._grid.remove(child)
        self._day_buttons.clear()
        self._heading.set_text(f"{_MONTH_LABELS[self._month]} {self._year}")

        weeks = _MONTH_GRID.monthdatescalendar(self._year, self._month)
        for row, week in enumerate(weeks):
            for col, cell_date in enumerate(week):
                btn = self._make_day_button(cell_date)
                self._grid.attach(btn, col, row, 1, 1)

        self._apply_marks()
        self._grid.show_all()

    def _make_day_button(self, cell_date: date) -> Gtk.Button:
        in_month = cell_date.month == self._month and cell_date.year == self._year
        btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("clock-calendar-day")
        if not in_month:
            btn.get_style_context().add_class("other-month")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        number = Gtk.Label(label=str(cell_date.day))
        number.get_style_context().add_class("clock-calendar-day-num")
        dot = Gtk.Label(label="")
        dot.get_style_context().add_class("clock-calendar-dot")
        box.pack_start(number, False, False, 0)
        box.pack_start(dot, False, False, 0)
        btn.add(box)

        if cell_date == self._selected:
            btn.get_style_context().add_class("selected")
        btn.connect("clicked", self._on_day_clicked, cell_date)
        if in_month:
            self._day_buttons[cell_date] = btn
        return btn

    def _on_day_clicked(self, _btn: Gtk.Button, picked: date) -> None:
        month_changed = picked.year != self._year or picked.month != self._month
        self._selected = picked
        if month_changed:
            self._year = picked.year
            self._month = picked.month
            self._rebuild_days()
            self._emit_month_changed()
        else:
            for btn in self._day_buttons.values():
                btn.get_style_context().remove_class("selected")
            active = self._day_buttons.get(picked)
            if active is not None:
                active.get_style_context().add_class("selected")
        if self._on_day_selected_cb is not None:
            self._on_day_selected_cb(picked)

    def _apply_marks(self) -> None:
        for cell_date, btn in self._day_buttons.items():
            mark = self._marks.get(cell_date.day)
            ctx = btn.get_style_context()
            ctx.remove_class("has-tasks")
            ctx.remove_class("has-overdue")
            dot = None
            for child in btn.get_children():
                if isinstance(child, Gtk.Box):
                    for label in child.get_children():
                        if (
                            isinstance(label, Gtk.Label)
                            and "clock-calendar-dot" in label.get_style_context().list_classes()
                        ):
                            dot = label
            if dot is None:
                continue
            if mark is None:
                dot.set_text("")
                continue
            _count, overdue, _titles = mark
            dot.set_text("●")
            ctx.add_class("has-tasks")
            if overdue:
                ctx.add_class("has-overdue")


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
        self._fixed_position: tuple[int, int] | None = None
        self._last_height = 0
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
        outer.set_size_request(248, -1)
        bus = event_bus
        if bus is None and isinstance(parent, Gtk.Window):
            bus = resolve_event_bus(parent)
        install_starfield(self, outer, bus)

        self._calendar = TaskMonthGrid()
        self._calendar.connect_day_selected(self._on_day_selected)
        self._calendar.connect_month_changed(self._on_month_changed)
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
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(shell_config.CLOCK_CALENDAR_TASKS_MAX_HEIGHT)
        scrolled.get_style_context().add_class("clock-calendar-tasks-scroll")
        scrolled.add(self._tasks_list)
        outer.pack_start(scrolled, False, False, 0)

        self._empty = Gtk.Label(label="Sin tareas este día", xalign=0)
        self._empty.get_style_context().add_class("clock-calendar-empty")

        if event_bus is not None and tasks_service is not None:
            event_bus.subscribe(TASKS_CHANGED, self._on_tasks_changed)
        self.connect("destroy", self._on_destroy)
        self.connect("size-allocate", self._on_size_allocate)

    def open_for(self, anchor: Gtk.Widget) -> None:
        self._anchor = anchor
        self._fixed_position = None
        self._last_height = 0
        self._select_today()
        self._refresh_marks()
        self._refresh_day_tasks()
        publish_popup_spawn(self, anchor, title=TITLE_CLOCK_CALENDAR, offset=8)
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
        self._calendar.select_date(today)
        self._selected = today
        self._refreshing_calendar = False

    def _on_day_selected(self, picked: date) -> None:
        if self._refreshing_calendar:
            return
        self._selected = picked
        self._refresh_day_tasks()

    def _on_month_changed(self, _grid: TaskMonthGrid) -> None:
        if self._refreshing_calendar:
            return
        self._refresh_marks()

    def _refresh_marks(self) -> None:
        if self._tasks_service is None:
            self._calendar.set_marks({})
            return
        year = self._calendar.visible_year
        month = self._calendar.visible_month
        today = date.today()
        marks: dict[int, tuple[int, bool, tuple[str, ...]]] = {}
        last = monthrange(year, month)[1]
        for day in range(1, last + 1):
            mark = calendar_day_mark(
                self._tasks_service.records(),
                date(year, month, day),
                today=today,
            )
            if mark is not None:
                marks[day] = mark
        self._calendar.set_marks(marks)

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

        for snapshot in items:
            occurrence = self._selected
            task_id = snapshot.id
            row = TaskRow(
                snapshot,
                on_toggle=lambda _tid, tid=task_id, when=occurrence: self._tasks_service.toggle(
                    tid,
                    on_date=when,
                ),
                compact=True,
                can_toggle=snapshot.status != "missed",
            )
            self._tasks_list.pack_start(row, False, False, 0)
        self._tasks_list.show_all()
        self._reposition()

    def _on_size_allocate(self, _widget: Gtk.Widget, allocation: Gtk.Allocation) -> None:
        height = int(allocation.height)
        if height <= 1 or height == self._last_height:
            return
        self._last_height = height
        self._reposition()

    def _reposition(self) -> None:
        if self.get_visible() and self._anchor is not None:
            schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._anchor is None:
            return False
        if self._fixed_position is not None:
            x, y = self._fixed_position
            reposition_popup(self, title=TITLE_CLOCK_CALENDAR, x=x, y=y)
            return False
        geometry = anchor_button_geometry(self._anchor)
        if geometry is None:
            return False
        popup_width, popup_height = popup_window_size(self)
        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        x, y = compute_popup_top_left(
            button_center_x=geometry.center_x,
            button_bottom=geometry.bottom,
            popup_width=popup_width,
            popup_height=popup_height,
            offset=8,
            monitor=monitor,
        )
        reposition_popup(self, title=TITLE_CLOCK_CALENDAR, x=x, y=y)
        self._fixed_position = (x, y)
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
        self._hover_surface.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._hover_surface.connect("button-press-event", self._on_clicked)

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
        if self._event_bus is not None:
            self._event_bus.subscribe(SETTINGS_CHANGED, self._on_settings_changed)
        self._refresh_display()
        self._schedule_next_tick()

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.strftime(shell_config.CLOCK_TIME_FORMAT)

    def _on_settings_changed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("key") in {
            "widgets.clock_time_format",
            "widgets.clock_date_format",
        }:
            self._refresh_display()

    def _refresh_display(self) -> None:
        now = datetime.now()
        self._time_label.set_text(self._format_time(now))
        self._date_label.set_text(now.strftime(shell_config.CLOCK_DATE_FORMAT))

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

    def _toggle_calendar(self) -> None:
        popup = self._ensure_calendar_popup()
        if popup.get_visible():
            self._outside_dismiss.uninstall()
            self._close_calendar()
            return
        self._open_calendar()

    def _on_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self._toggle_calendar()
        return True

    def _on_destroy(self, *_args) -> None:
        self._outside_dismiss.uninstall()
        if self._event_bus is not None:
            self._event_bus.unsubscribe(SETTINGS_CHANGED, self._on_settings_changed)
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
        if self._calendar_popup is not None:
            self._calendar_popup.destroy()
            self._calendar_popup = None
