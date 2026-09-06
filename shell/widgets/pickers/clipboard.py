"""Clipboard picker overlay, visually a Search sibling."""

from __future__ import annotations

from collections.abc import Callable
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import GLib, Gtk, Pango

from ...config import CLIPBOARD_PREVIEW_CHARS, CLIPBOARD_PREVIEW_LINES, LAUNCHER_ROW_ICON_SIZE
from ...identity import TITLE_CLIPBOARD_PICKER
from ...servicios.portapapeles.historia import (
    ClipboardEntry,
    format_copied_ago,
    preview_text,
    search_entries,
)
from .overlay import PickerOverlay
from .session import PickerSession


class ClipboardRow(Gtk.ListBoxRow):
    def __init__(self, entry: ClipboardEntry, *, now: float) -> None:
        super().__init__()
        self.entry = entry
        self.get_style_context().add_class("launcher-row")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.get_style_context().add_class("launcher-row-content")
        self.add(content)

        icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.DIALOG)
        icon.set_pixel_size(LAUNCHER_ROW_ICON_SIZE)
        content.pack_start(icon, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(
            label=preview_text(
                entry.text,
                max_chars=CLIPBOARD_PREVIEW_CHARS,
                max_lines=CLIPBOARD_PREVIEW_LINES,
            ),
            xalign=0,
        )
        name.get_style_context().add_class("launcher-row-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        labels.pack_start(name, False, False, 0)

        comment = Gtk.Label(label=format_copied_ago(entry.copied_at, now=now), xalign=0)
        comment.get_style_context().add_class("launcher-row-comment")
        comment.set_ellipsize(Pango.EllipsizeMode.END)
        labels.pack_start(comment, False, False, 0)
        content.pack_start(labels, True, True, 0)


class ClipboardPickerWindow(PickerOverlay):
    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_refresh: Callable[[], tuple[ClipboardEntry, ...]],
        on_copy: Callable[[str], None],
    ) -> None:
        super().__init__(
            shell_window,
            window_name="shell-clipboard-picker",
            title=TITLE_CLIPBOARD_PICKER,
            namespace="shell-clipboard-picker",
            placeholder="Buscar en el portapapeles...",
            empty_text="Sin resultados",
            session=PickerSession(columns=1),
        )
        self._on_refresh = on_refresh
        self._on_copy = on_copy
        self._entries: tuple[ClipboardEntry, ...] = ()
        self._rows: tuple[ClipboardRow, ...] = ()

        self._list = Gtk.ListBox()
        self._list.get_style_context().add_class("launcher-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-activated", self._on_row_activated)
        self._scrolled = self.attach_scrolled(self._list)

    def set_entries(self, entries: tuple[ClipboardEntry, ...]) -> None:
        self._entries = entries
        if self.get_visible():
            self._rebuild_rows(keep_selection=True)

    def on_prepare_open(self) -> None:
        self._entries = self._on_refresh()

    def on_query_changed(self, query: str) -> None:
        self._rebuild_rows()

    def on_activate(self) -> None:
        row = self._list.get_selected_row()
        if isinstance(row, ClipboardRow):
            self._select_entry(row.entry)

    def on_selection_moved(self) -> None:
        index = self.session.selected_index
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            self._list.select_row(row)
            GLib.idle_add(self._ensure_row_visible, row)

    def _rebuild_rows(self, *, keep_selection: bool = False) -> None:
        selected_id = None
        if keep_selection:
            current = self._list.get_selected_row()
            if isinstance(current, ClipboardRow):
                selected_id = current.entry.id
        for child in list(self._list.get_children()):
            self._list.remove(child)

        matches = search_entries(self._entries, self._search.get_text())
        now = time.time()
        rows: list[ClipboardRow] = []
        for entry in matches:
            row = ClipboardRow(entry, now=now)
            self._list.add(row)
            rows.append(row)
        self._rows = tuple(rows)
        self._list.show_all()
        self.session.set_items(len(rows), reset_selection=not keep_selection)

        if rows:
            self.set_empty_visible(False)
            self._list.show()
            chosen = next((row for row in rows if row.entry.id == selected_id), rows[self.session.selected_index])
            self.session.select_index(rows.index(chosen))
            self._list.select_row(chosen)
        else:
            self._list.hide()
            self.set_empty_visible(True)

    def _select_entry(self, entry: ClipboardEntry) -> None:
        self._on_copy(entry.id)
        self.close_picker()

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, ClipboardRow):
            self._select_entry(row.entry)

    def _ensure_row_visible(self, row: Gtk.ListBoxRow) -> bool:
        alloc = row.get_allocation()
        adj = self._scrolled.get_vadjustment()
        if adj is None:
            return False
        value = adj.get_value()
        page = adj.get_page_size()
        if alloc.y < value:
            adj.set_value(alloc.y)
        elif alloc.y + alloc.height > value + page:
            adj.set_value(alloc.y + alloc.height - page)
        return False
