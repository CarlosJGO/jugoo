"""Emoji picker overlay, visually a Search sibling with a glyph grid."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import GLib, Gtk

from ...config import EMOJI_PICKER_COLUMNS
from ...identity import TITLE_EMOJI_PICKER
from ...servicios.emojis.catalogo import EmojiRecord, search_emojis
from .overlay import PickerOverlay
from .session import PickerSession


class EmojiCell(Gtk.FlowBoxChild):
    def __init__(self, emoji: EmojiRecord) -> None:
        super().__init__()
        self.emoji = emoji
        self.get_style_context().add_class("picker-emoji-cell")
        label = Gtk.Label(label=emoji.glyph)
        label.get_style_context().add_class("picker-emoji-glyph")
        tooltip = emoji.name
        if emoji.aliases:
            tooltip = f"{emoji.name} · {emoji.aliases[0]}"
        self.set_tooltip_text(tooltip)
        self.add(label)


class EmojiPickerWindow(PickerOverlay):
    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_refresh: Callable[[], tuple[EmojiRecord, ...]],
        on_copy: Callable[[str], bool],
    ) -> None:
        super().__init__(
            shell_window,
            window_name="shell-emoji-picker",
            title=TITLE_EMOJI_PICKER,
            namespace="shell-emoji-picker",
            placeholder="Buscar emoji...",
            empty_text="Sin resultados",
            session=PickerSession(columns=EMOJI_PICKER_COLUMNS),
        )
        self._on_refresh = on_refresh
        self._on_copy = on_copy
        self._catalog: tuple[EmojiRecord, ...] = ()
        self._visible: tuple[EmojiCell, ...] = ()

        self._flow = Gtk.FlowBox()
        self._flow.get_style_context().add_class("picker-emoji-grid")
        self._flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._flow.set_activate_on_single_click(True)
        self._flow.set_homogeneous(True)
        self._flow.set_min_children_per_line(EMOJI_PICKER_COLUMNS)
        self._flow.set_max_children_per_line(EMOJI_PICKER_COLUMNS)
        self._flow.set_column_spacing(2)
        self._flow.set_row_spacing(2)
        self._flow.connect("child-activated", self._on_child_activated)
        self._scrolled = self.attach_scrolled(self._flow)

    def on_prepare_open(self) -> None:
        if not self._catalog:
            self._catalog = self._on_refresh()
            self._build_catalog()

    def on_query_changed(self, query: str) -> None:
        self._apply_filter(query)

    def on_activate(self) -> None:
        index = self.session.selected_index
        if 0 <= index < len(self._visible):
            self._select_emoji(self._visible[index].emoji)

    def on_selection_moved(self) -> None:
        index = self.session.selected_index
        if 0 <= index < len(self._visible):
            child = self._visible[index]
            self._flow.select_child(child)
            GLib.idle_add(self._ensure_child_visible, child)

    def _build_catalog(self) -> None:
        for child in list(self._flow.get_children()):
            self._flow.remove(child)
        for emoji in self._catalog:
            self._flow.add(EmojiCell(emoji))
        self._flow.show_all()

    def _apply_filter(self, query: str) -> None:
        matches = search_emojis(self._catalog, query)
        wanted = {emoji.glyph for emoji in matches}
        visible: list[EmojiCell] = []
        for child in self._flow.get_children():
            if not isinstance(child, EmojiCell):
                continue
            show = child.emoji.glyph in wanted
            child.set_visible(show)
            if show:
                visible.append(child)
        self._visible = tuple(visible)
        self.session.set_items(len(visible), reset_selection=True)
        if visible:
            self.set_empty_visible(False)
            self._flow.show()
            self._flow.select_child(visible[0])
        else:
            self.set_empty_visible(True)

    def _select_emoji(self, emoji: EmojiRecord) -> None:
        if self._on_copy(emoji.glyph):
            self.close_picker()

    def _on_child_activated(self, _flow: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        if isinstance(child, EmojiCell):
            self._select_emoji(child.emoji)

    def _ensure_child_visible(self, child: Gtk.FlowBoxChild) -> bool:
        alloc = child.get_allocation()
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
