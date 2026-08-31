from __future__ import annotations

from datetime import datetime

from shell.widgets.barra.clock import ClockWidget


def test_clock_time_uses_12h_with_am_pm() -> None:
    assert ClockWidget._format_time(datetime(2024, 1, 1, 13, 5)) == "01:05 PM"
    assert ClockWidget._format_time(datetime(2024, 1, 1, 0, 7)) == "12:07 AM"
