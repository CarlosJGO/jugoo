"""ISO date entry with a mini-calendar popup."""

from __future__ import annotations

from datetime import date
from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ..popup_handle import register_owned_surface, unregister_owned_surface


class DateEntry(Gtk.Box):
    """Editable ``AAAA-MM-DD`` field that opens a calendar for picking a day.

    Uses a transient ``Gtk.WindowType.POPUP`` (same pattern as ComboBox menus)
    so shell outside-click dismiss treats the calendar as part of the owner
    window on Wayland.
    """

    def __init__(self, *, initial: date | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.get_style_context().add_class("date-entry")
        self._suppress_calendar = False
        self._changed_handlers: list[Callable[[date], None]] = []

        self._entry = Gtk.Entry()
        self._entry.get_style_context().add_class("date-entry-field")
        self._entry.set_placeholder_text("AAAA-MM-DD")
        self._entry.set_width_chars(12)
        self._entry.set_max_length(10)
        self._entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY,
            "x-office-calendar-symbolic",
        )
        self._entry.set_icon_activatable(Gtk.EntryIconPosition.SECONDARY, True)
        self._entry.set_icon_tooltip_text(
            Gtk.EntryIconPosition.SECONDARY,
            "Elegir fecha",
        )
        self._entry.connect("icon-press", self._on_icon_press)
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("focus-out-event", self._on_entry_focus_out)
        self.pack_start(self._entry, True, True, 0)

        self._cal_window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._cal_window.set_type_hint(Gdk.WindowTypeHint.COMBO)
        self._cal_window.set_resizable(False)
        self._cal_window.set_decorated(False)
        self._cal_window.set_skip_taskbar_hint(True)
        self._cal_window.set_skip_pager_hint(True)
        self._cal_window.set_accept_focus(True)
        self._cal_window.get_style_context().add_class("date-entry-popover")
        self._cal_window.connect("button-press-event", self._on_cal_window_button)
        self._cal_window.connect("key-press-event", self._on_cal_window_key)
        self._cal_window.connect("map", self._on_cal_map)
        self._cal_window.connect("unmap", self._on_cal_unmap)

        self._calendar = Gtk.Calendar()
        self._calendar.get_style_context().add_class("date-entry-calendar")
        self._calendar.connect("day-selected", self._on_day_selected)
        self._cal_window.add(self._calendar)
        self._calendar.show_all()

        self.connect("destroy", self._on_destroy)
        self.set_date(initial or date.today())

    def connect_changed(self, handler: Callable[[date], None]) -> None:
        self._changed_handlers.append(handler)

    def get_entry(self) -> Gtk.Entry:
        return self._entry

    def get_text(self) -> str:
        return self._entry.get_text().strip()

    def set_text(self, value: str) -> None:
        text = (value or "").strip()
        parsed = _parse_iso_date(text)
        if parsed is not None:
            self.set_date(parsed)
            return
        self._entry.set_text(text)

    def get_date(self) -> date | None:
        return _parse_iso_date(self.get_text())

    def set_date(self, value: date) -> None:
        self._suppress_calendar = True
        try:
            self._entry.set_text(value.isoformat())
            self._calendar.select_month(value.month - 1, value.year)
            self._calendar.select_day(value.day)
        finally:
            self._suppress_calendar = False

    def set_width_chars(self, width: int) -> None:
        self._entry.set_width_chars(width)

    def set_placeholder_text(self, text: str) -> None:
        self._entry.set_placeholder_text(text)

    def grab_focus(self) -> None:
        self._entry.grab_focus()

    def open_calendar(self) -> None:
        current = self.get_date() or date.today()
        self._suppress_calendar = True
        try:
            self._calendar.select_month(current.month - 1, current.year)
            self._calendar.select_day(current.day)
        finally:
            self._suppress_calendar = False

        owner = self.get_toplevel()
        if isinstance(owner, Gtk.Window):
            self._cal_window.set_transient_for(owner)
            try:
                self._cal_window.set_attached_to(self._entry)
            except Exception:
                pass

        self._cal_window.show_all()
        self._position_calendar()
        self._cal_window.present()

    def close_calendar(self) -> None:
        if self._cal_window.get_visible():
            self._cal_window.hide()

    def _position_calendar(self) -> None:
        """Place the popup under the entry when the platform allows moves."""
        gdk_entry = self._entry.get_window()
        if gdk_entry is None:
            return
        try:
            _origin, origin_x, origin_y = gdk_entry.get_origin()
        except Exception:
            return
        allocation = self._entry.get_allocation()
        # Entry may not own its Gdk window; allocation is relative to that window.
        if self._entry.get_has_window():
            left, top = 0, 0
        else:
            left, top = int(allocation.x), int(allocation.y)
        x = int(origin_x) + left
        y = int(origin_y) + top + int(allocation.height) + 4
        try:
            self._cal_window.move(x, y)
        except Exception:
            pass

    def _on_icon_press(self, _entry, icon_pos, event) -> None:
        if icon_pos != Gtk.EntryIconPosition.SECONDARY:
            return
        button = 1
        try:
            ok, value = event.get_button()
            if ok:
                button = int(value)
        except Exception:
            button = int(getattr(event, "button", 1) or 1)
        if button != 1:
            return
        if self._cal_window.get_visible():
            self.close_calendar()
        else:
            self.open_calendar()

    def _on_entry_activate(self, *_args) -> None:
        self._normalize_entry()

    def _on_entry_focus_out(self, *_args) -> bool:
        self._normalize_entry()
        return False

    def _normalize_entry(self) -> None:
        parsed = self.get_date()
        if parsed is not None:
            self.set_date(parsed)
            self._emit_changed(parsed)

    def _on_day_selected(self, *_args) -> None:
        if self._suppress_calendar or not self._cal_window.get_visible():
            return
        self._apply_calendar_day(close=True)

    def _apply_calendar_day(self, *, close: bool) -> None:
        year, month, day = self._calendar.get_date()
        try:
            chosen = date(int(year), int(month) + 1, int(day))
        except ValueError:
            return
        self.set_date(chosen)
        self._emit_changed(chosen)
        if close:
            GLib.idle_add(self.close_calendar)

    def _emit_changed(self, value: date) -> None:
        for handler in list(self._changed_handlers):
            handler(value)

    def _on_cal_window_button(self, _window, event) -> bool:
        # Keep presses inside the calendar from bubbling as "outside".
        return False

    def _on_cal_window_key(self, _window, event) -> bool:
        keyval = getattr(event, "keyval", None)
        if keyval in (Gdk.KEY_Escape,):
            self.close_calendar()
            return True
        return False

    def _on_cal_map(self, *_args) -> None:
        register_owned_surface(self, self._cal_window)

    def _on_cal_unmap(self, *_args) -> None:
        unregister_owned_surface(self, self._cal_window)

    def _on_destroy(self, *_args) -> None:
        self.close_calendar()
        unregister_owned_surface(self, self._cal_window)
        self._cal_window.destroy()


def _parse_iso_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
