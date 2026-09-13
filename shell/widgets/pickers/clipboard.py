"""Clipboard picker puerta: history list + full detail pane in one card."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from ...config import (
    CLIPBOARD_DETAIL_IMAGE_MAX,
    CLIPBOARD_LIST_WIDTH,
    CLIPBOARD_PICKER_HEIGHT,
    CLIPBOARD_PICKER_WIDTH,
    CLIPBOARD_PREVIEW_CHARS,
    CLIPBOARD_PREVIEW_LINES,
    CLIPBOARD_THUMBNAIL_SIZE,
    LAUNCHER_ROW_ICON_SIZE,
)
from ...identity import TITLE_CLIPBOARD_PICKER
from ...servicios.portapapeles.historia import (
    ClipboardEntry,
    find_match_spans,
    format_copied_ago,
    preview_match,
    preview_text,
    search_entries,
)
from ...ui.theme import active_theme
from .overlay import PickerOverlay
from .session import PickerSession


def _load_pixbuf(path: Path, size: int) -> GdkPixbuf.Pixbuf | None:
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
    except GLib.Error:
        return None


def _parse_hex_rgba(value: str, *, alpha: float = 1.0) -> Gdk.RGBA:
    color = Gdk.RGBA()
    cleaned = value.strip()
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"
    if not color.parse(cleaned):
        color.parse("#7C8CFF")
    color.alpha = max(0.0, min(1.0, alpha))
    return color


def _highlight_markup(text: str, query: str, *, accent: str) -> str:
    """Escape ``text`` and wrap query hits in a colored span."""
    spans = find_match_spans(text, query)
    if not spans:
        return GLib.markup_escape_text(text)
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        if start < cursor:
            continue
        pieces.append(GLib.markup_escape_text(text[cursor:start]))
        hit = GLib.markup_escape_text(text[start:end])
        pieces.append(
            f'<span background="{accent}" foreground="#04060E" weight="bold">{hit}</span>'
        )
        cursor = end
    pieces.append(GLib.markup_escape_text(text[cursor:]))
    return "".join(pieces)


class ClipboardRow(Gtk.ListBoxRow):
    def __init__(
        self,
        entry: ClipboardEntry,
        *,
        now: float,
        image_path: Path | None = None,
        query: str = "",
    ) -> None:
        super().__init__()
        self.entry = entry
        self.get_style_context().add_class("launcher-row")
        if entry.is_image:
            self.get_style_context().add_class("clipboard-row-image")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.get_style_context().add_class("launcher-row-content")
        self.add(content)

        if entry.is_image:
            thumb = Gtk.Image()
            thumb.get_style_context().add_class("clipboard-row-thumb")
            pixbuf = None if image_path is None else _load_pixbuf(
                image_path, CLIPBOARD_THUMBNAIL_SIZE
            )
            if pixbuf is not None:
                thumb.set_from_pixbuf(pixbuf)
            else:
                thumb.set_from_icon_name("image-x-generic-symbolic", Gtk.IconSize.DIALOG)
                thumb.set_pixel_size(CLIPBOARD_THUMBNAIL_SIZE)
            content.pack_start(thumb, False, False, 0)
            title = "Imagen"
        else:
            icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.DIALOG)
            icon.set_pixel_size(LAUNCHER_ROW_ICON_SIZE)
            content.pack_start(icon, False, False, 0)
            title = (
                preview_match(
                    entry.text,
                    query,
                    max_chars=CLIPBOARD_PREVIEW_CHARS,
                    max_lines=CLIPBOARD_PREVIEW_LINES,
                )
                if query.strip()
                else preview_text(
                    entry.text,
                    max_chars=CLIPBOARD_PREVIEW_CHARS,
                    max_lines=CLIPBOARD_PREVIEW_LINES,
                )
            )

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(xalign=0)
        name.get_style_context().add_class("launcher-row-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        theme = active_theme()
        accent = theme.colors.primary if theme is not None else "#7C8CFF"
        if query.strip() and not entry.is_image:
            name.set_use_markup(True)
            name.set_markup(_highlight_markup(title, query, accent=accent))
        else:
            name.set_text(title)
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
        resolve_image: Callable[[ClipboardEntry], Path | None] | None = None,
    ) -> None:
        super().__init__(
            shell_window,
            window_name="shell-clipboard-picker",
            title=TITLE_CLIPBOARD_PICKER,
            namespace="shell-clipboard-picker",
            placeholder="Pista: un pedazo del texto…",
            empty_text="Sin resultados",
            session=PickerSession(columns=1),
            card_width=CLIPBOARD_PICKER_WIDTH,
            card_height=CLIPBOARD_PICKER_HEIGHT,
        )
        self._on_refresh = on_refresh
        self._on_copy = on_copy
        self._resolve_image = resolve_image or (lambda _entry: None)
        self._entries: tuple[ClipboardEntry, ...] = ()
        self._rows: tuple[ClipboardRow, ...] = ()
        self._detail_entry_id: str | None = None
        self._detail_query: str = ""
        self._hit_tag: Gtk.TextTag | None = None

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.get_style_context().add_class("clipboard-split")
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content_box.pack_start(body, True, True, 0)

        list_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        list_column.get_style_context().add_class("clipboard-list-pane")
        list_column.set_size_request(CLIPBOARD_LIST_WIDTH, -1)
        list_column.set_hexpand(False)
        list_column.set_vexpand(True)
        body.pack_start(list_column, False, True, 0)

        self._list = Gtk.ListBox()
        self._list.get_style_context().add_class("launcher-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # Click selects + previews; Enter / double-click pastes.
        self._list.set_activate_on_single_click(False)
        self._list.connect("row-selected", self._on_row_selected)
        self._list.connect("row-activated", self._on_row_activated)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.get_style_context().add_class("launcher-scroll")
        self._scrolled.add(self._list)
        list_column.pack_start(self._scrolled, True, True, 0)

        detail_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail_column.get_style_context().add_class("clipboard-detail-pane")
        detail_column.set_hexpand(True)
        detail_column.set_vexpand(True)
        body.pack_start(detail_column, True, True, 0)

        self._detail_meta = Gtk.Label(label="", xalign=0)
        self._detail_meta.get_style_context().add_class("clipboard-detail-meta")
        self._detail_meta.set_ellipsize(Pango.EllipsizeMode.END)
        detail_column.pack_start(self._detail_meta, False, False, 0)

        self._detail_stack = Gtk.Stack()
        self._detail_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._detail_stack.set_transition_duration(120)
        self._detail_stack.set_vexpand(True)
        self._detail_stack.set_hexpand(True)
        detail_column.pack_start(self._detail_stack, True, True, 0)

        self._detail_empty = Gtk.Label(label="Selecciona una entrada para verla completa")
        self._detail_empty.get_style_context().add_class("clipboard-detail-empty")
        self._detail_empty.set_line_wrap(True)
        self._detail_empty.set_justify(Gtk.Justification.CENTER)
        self._detail_stack.add_named(self._detail_empty, "empty")

        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        text_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        text_scroll.get_style_context().add_class("clipboard-detail-scroll")
        text_scroll.set_vexpand(True)
        text_scroll.set_hexpand(True)
        self._detail_text_scroll = text_scroll

        self._detail_text = Gtk.TextView()
        self._detail_text.get_style_context().add_class("clipboard-detail-text")
        self._detail_text.set_editable(False)
        self._detail_text.set_cursor_visible(False)
        self._detail_text.set_can_focus(False)
        self._detail_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._detail_text.set_accepts_tab(False)
        self._detail_text.set_left_margin(10)
        self._detail_text.set_right_margin(10)
        self._detail_text.set_top_margin(8)
        self._detail_text.set_bottom_margin(8)
        buffer = self._detail_text.get_buffer()
        theme = active_theme()
        accent = theme.colors.primary if theme is not None else "#7C8CFF"
        self._hit_tag = buffer.create_tag(
            "clipboard-search-hit",
            background_rgba=_parse_hex_rgba(accent, alpha=0.55),
            foreground_rgba=_parse_hex_rgba("#04060E"),
            weight=Pango.Weight.BOLD,
        )
        text_scroll.add(self._detail_text)
        self._detail_stack.add_named(text_scroll, "text")

        image_scroll = Gtk.ScrolledWindow()
        image_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        image_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        image_scroll.get_style_context().add_class("clipboard-detail-scroll")
        image_scroll.set_vexpand(True)
        image_scroll.set_hexpand(True)

        image_align = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        image_align.set_halign(Gtk.Align.CENTER)
        image_align.set_valign(Gtk.Align.CENTER)
        image_align.set_hexpand(True)
        image_align.set_vexpand(True)

        self._detail_image = Gtk.Image()
        self._detail_image.get_style_context().add_class("clipboard-detail-image")
        image_align.pack_start(self._detail_image, True, True, 0)
        image_scroll.add(image_align)
        self._detail_stack.add_named(image_scroll, "image")

        self._show_detail(None)

    def set_entries(self, entries: tuple[ClipboardEntry, ...]) -> None:
        self._entries = entries
        if self.get_visible():
            self._rebuild_rows(keep_selection=True)

    def on_prepare_open(self) -> None:
        self._entries = self._on_refresh()
        self._detail_entry_id = None
        self._detail_query = ""

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
            self._show_detail(row.entry)

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
        query = self._search.get_text()
        rows: list[ClipboardRow] = []
        for entry in matches:
            image_path = self._resolve_image(entry) if entry.is_image else None
            row = ClipboardRow(entry, now=now, image_path=image_path, query=query)
            self._list.add(row)
            rows.append(row)
        self._rows = tuple(rows)
        self._list.show_all()
        self.session.set_items(len(rows), reset_selection=not keep_selection)

        if rows:
            self.set_empty_visible(False)
            self._list.show()
            chosen = next(
                (row for row in rows if row.entry.id == selected_id),
                rows[self.session.selected_index],
            )
            self.session.select_index(rows.index(chosen))
            self._list.select_row(chosen)
            self._show_detail(chosen.entry)
        else:
            self._list.hide()
            self.set_empty_visible(True)
            self._show_detail(None)

    def _show_detail(self, entry: ClipboardEntry | None) -> None:
        query = self._search.get_text()
        if entry is None:
            self._detail_entry_id = None
            self._detail_query = ""
            self._detail_meta.set_text("")
            self._detail_text.get_buffer().set_text("")
            self._detail_image.clear()
            self._detail_stack.set_visible_child_name("empty")
            return

        same_entry = entry.id == self._detail_entry_id
        same_query = query == self._detail_query
        if same_entry and same_query:
            return

        ago = format_copied_ago(entry.copied_at, now=time.time())

        if entry.is_image:
            self._detail_entry_id = entry.id
            self._detail_query = query
            path = self._resolve_image(entry)
            mime = entry.mime or "image/png"
            self._detail_meta.set_text(f"Imagen · {mime} · {ago}")
            pixbuf = None if path is None else _load_pixbuf(path, CLIPBOARD_DETAIL_IMAGE_MAX)
            if pixbuf is not None:
                self._detail_image.set_from_pixbuf(pixbuf)
            else:
                self._detail_image.set_from_icon_name(
                    "image-x-generic-symbolic", Gtk.IconSize.DIALOG
                )
                self._detail_image.set_pixel_size(96)
            self._detail_text.get_buffer().set_text("")
            self._detail_stack.set_visible_child_name("image")
            return

        chars = len(entry.text)
        lines = entry.text.count("\n") + (1 if entry.text else 0)
        self._detail_meta.set_text(f"{chars} caracteres · {lines} líneas · {ago}")
        if not same_entry:
            self._detail_text.get_buffer().set_text(entry.text)
            adj = self._detail_text_scroll.get_vadjustment()
            if adj is not None:
                adj.set_value(0)
        self._detail_entry_id = entry.id
        self._detail_query = query
        self._detail_image.clear()
        self._detail_stack.set_visible_child_name("text")
        self._apply_search_highlights(entry.text, query)

    def _apply_search_highlights(self, text: str, query: str) -> None:
        buffer = self._detail_text.get_buffer()
        tag = self._hit_tag
        if tag is None:
            return
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        buffer.remove_tag(tag, start, end)
        spans = find_match_spans(text, query)
        if not spans:
            return
        for hit_start, hit_end in spans:
            buffer.apply_tag(
                tag,
                buffer.get_iter_at_offset(hit_start),
                buffer.get_iter_at_offset(hit_end),
            )
        first = buffer.get_iter_at_offset(spans[0][0])
        GLib.idle_add(self._scroll_detail_to_iter, first)

    def _scroll_detail_to_iter(self, text_iter: Gtk.TextIter) -> bool:
        self._detail_text.scroll_to_iter(text_iter, 0.15, True, 0.0, 0.25)
        return False

    def _select_entry(self, entry: ClipboardEntry) -> None:
        self._on_copy(entry.id)
        self.close_picker()

    def _on_row_selected(
        self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if isinstance(row, ClipboardRow):
            if row in self._rows:
                self.session.select_index(self._rows.index(row))
            self._show_detail(row.entry)
        elif row is None and not self._rows:
            self._show_detail(None)

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
