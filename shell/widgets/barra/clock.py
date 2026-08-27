"""Clock module showing current time and a compact date."""

from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ...config import CLOCK_DATE_FORMAT, CLOCK_TIME_FORMAT
from ...ui import SHELL_MODULE_STACK_SPACING, ShellModule, shell_label


class ClockWidget(ShellModule):
    """Self-contained clock that ticks on GTK's main loop."""

    def __init__(self) -> None:
        super().__init__(
            "clock-widget",
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SHELL_MODULE_STACK_SPACING,
        )
        self._tick_source_id: int | None = None

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

        self.pack_start(self._time_label, False, False, 0)
        self.pack_start(self._date_label, False, False, 0)

        self.connect("destroy", self._on_destroy)
        self._refresh_display()
        self._schedule_next_tick()

    def apply_shell_compact(self, compact: bool) -> None:
        if compact:
            self._date_label.hide()
        else:
            self._date_label.show()

    def _refresh_display(self) -> None:
        now = datetime.now()
        self._time_label.set_text(now.strftime(CLOCK_TIME_FORMAT))
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

    def _on_destroy(self, *_args) -> None:
        if self._tick_source_id is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = None
