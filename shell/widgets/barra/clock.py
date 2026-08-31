"""Clock module showing current time and a compact date."""

from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import CLOCK_DATE_FORMAT, CLOCK_TIME_FORMAT
from ...popup_handle import hide_popup, present_popup
from ...ui import SHELL_MODULE_STACK_SPACING, ShellModule, shell_label
from ...window_identity import (
    TITLE_CLOCK_CALENDAR,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)


class ClockCalendarPopup(Gtk.Window):
    """Interactive calendar popup anchored to the clock block."""

    def __init__(self, anchor: Gtk.Widget, clock_widget: object | None = None) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._anchor: Gtk.Widget | None = anchor
        self._fixed_top: int | None = None
        self._clock_widget = clock_widget

        self.set_name("shell-clock-calendar")
        parent = anchor.get_toplevel()
        if isinstance(parent, Gtk.Window):
            register_shell_popup(self, parent)
        configure_toplevel(self, title=TITLE_CLOCK_CALENDAR)
        configure_interactive_popup(self)
        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("enter-notify-event", self._on_pointer_enter)
        self.connect("leave-notify-event", self._on_pointer_leave)

        calendar = Gtk.Calendar()
        calendar.set_display_options(
            Gtk.CalendarDisplayOptions.SHOW_HEADING
            | Gtk.CalendarDisplayOptions.SHOW_DAY_NAMES
        )
        self.add(calendar)

    def open_for(self, anchor: Gtk.Widget) -> None:
        self._anchor = anchor
        self._fixed_top = None
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close(self) -> None:
        hide_popup(self)

    def _on_pointer_enter(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        if self._clock_widget is not None and hasattr(self._clock_widget, "_on_popup_pointer_enter"):
            self._clock_widget._on_popup_pointer_enter()
        return False

    def _on_pointer_leave(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        if self._clock_widget is not None and hasattr(self._clock_widget, "_on_popup_pointer_leave"):
            self._clock_widget._on_popup_pointer_leave()
        return False

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

    def __init__(self) -> None:
        super().__init__(
            "clock-widget",
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SHELL_MODULE_STACK_SPACING,
        )
        self._tick_source_id: int | None = None
        self._calendar_popup: ClockCalendarPopup | None = None
        self._clock_hovered = False
        self._popup_hovered = False

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
        self._hover_surface.connect("leave-notify-event", self._on_pointer_leave)

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
            self._calendar_popup = ClockCalendarPopup(self, self)
        return self._calendar_popup

    def _close_calendar_if_unhovered(self) -> None:
        if self._clock_hovered or self._popup_hovered:
            return
        if self._calendar_popup is not None:
            self._calendar_popup.close()

    def _on_popup_pointer_enter(self) -> None:
        self._popup_hovered = True

    def _on_popup_pointer_leave(self) -> None:
        self._popup_hovered = False
        self._close_calendar_if_unhovered()

    def _on_pointer_enter(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self._clock_hovered = True
        popup = self._ensure_calendar_popup()
        if not popup.get_visible():
            popup.open_for(self)
        return False

    def _on_pointer_leave(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self._clock_hovered = False
        self._close_calendar_if_unhovered()
        return False

    def _on_destroy(self, *_args) -> None:
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
        if self._calendar_popup is not None:
            self._calendar_popup.destroy()
            self._calendar_popup = None
