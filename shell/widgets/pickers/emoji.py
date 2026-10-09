"""Emoji picker puerta, visually a Search sibling with a glyph grid."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import EMOJI_PICKER_COLUMNS
from ...identity import TITLE_EMOJI_PICKER
from ...servicios.emojis.catalogo import EmojiRecord, search_emojis
from .overlay import PickerOverlay
from .session import (
    ACTION_CLOSE,
    ACTION_MOVED,
    ACTION_SELECT,
    KEY_DOWN,
    KEY_KP_DOWN,
    KEY_KP_LEFT,
    KEY_KP_RIGHT,
    KEY_KP_UP,
    KEY_LEFT,
    KEY_RIGHT,
    KEY_UP,
    PickerSession,
    scroll_to_reveal,
)


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
        on_recent: Callable[[], tuple[EmojiRecord, ...]],
        on_copy: Callable[[str], None],
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
        self._on_recent = on_recent
        self._on_copy = on_copy
        self._catalog: tuple[EmojiRecord, ...] = ()
        self._recent: tuple[EmojiRecord, ...] = ()
        self._visible: tuple[EmojiCell, ...] = ()
        self._positions: tuple[tuple[int, int], ...] = ()

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._recent_title = self._section_title("Recientes")
        self._content.pack_start(self._recent_title, False, False, 0)
        self._recent_flow = self._new_flow("picker-emoji-grid")
        self._recent_flow.connect("child-activated", self._on_child_activated)
        self._content.pack_start(self._recent_flow, False, False, 0)
        all_title = self._section_title("Todos")
        self._content.pack_start(all_title, False, False, 0)
        self._flow = Gtk.FlowBox()
        self._configure_flow(self._flow, "picker-emoji-grid")
        self._flow.connect("child-activated", self._on_child_activated)
        self._content.pack_start(self._flow, False, False, 0)
        self._scrolled = self.attach_scrolled(self._content)

    def on_prepare_open(self) -> None:
        if not self._catalog:
            self._catalog = self._on_refresh()
            self._build_catalog()
        self._recent = self._on_recent()
        self._build_recent()

    def on_query_changed(self, query: str) -> None:
        self._apply_filter(query)

    def on_after_show_all(self) -> None:
        self._apply_filter(self.session.query)

    def on_activate(self) -> None:
        index = self.session.selected_index
        if 0 <= index < len(self._visible):
            self._select_emoji(self._visible[index].emoji)

    def on_selection_moved(self) -> None:
        index = self.session.selected_index
        if 0 <= index < len(self._visible):
            child = self._visible[index]
            self._flow.unselect_all()
            self._recent_flow.unselect_all()
            parent = child.get_parent()
            if isinstance(parent, Gtk.FlowBox):
                parent.select_child(child)
            GLib.idle_add(self._ensure_child_visible, child)

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key_name = Gdk.keyval_name(event.keyval) or ""
        directions = {
            KEY_UP: (0, -1),
            KEY_KP_UP: (0, -1),
            KEY_DOWN: (0, 1),
            KEY_KP_DOWN: (0, 1),
            KEY_LEFT: (-1, 0),
            KEY_KP_LEFT: (-1, 0),
            KEY_RIGHT: (1, 0),
            KEY_KP_RIGHT: (1, 0),
        }
        if key_name in directions:
            self._move_to_neighbor(*directions[key_name])
            return True

        result = self.session.handle_key(key_name)
        if result == ACTION_CLOSE:
            self.close_picker()
            return True
        if result == ACTION_SELECT:
            self.on_activate()
            return True
        if result == ACTION_MOVED:
            self.on_selection_moved()
            return True
        return False

    def _move_to_neighbor(self, dx: int, dy: int) -> None:
        if not self.session.open or not self._positions:
            return
        index = self.session.selected_index
        if not 0 <= index < len(self._positions):
            index = 0
        row, column = self._positions[index]
        target_row = row + dy
        target_column = column + dx
        candidates = [
            (candidate_column, candidate_index)
            for candidate_index, (candidate_row, candidate_column) in enumerate(self._positions)
            if candidate_row == target_row
            and (dy != 0 or candidate_column == target_column)
            and (dx == 0 or (candidate_column - column) * dx > 0)
        ]
        if not candidates:
            return
        if dy != 0:
            _candidate_column, target_index = min(
                candidates,
                key=lambda candidate: abs(candidate[0] - column),
            )
        else:
            _candidate_column, target_index = min(
                candidates,
                key=lambda candidate: abs(candidate[0] - target_column),
            )
        self.session.select_index(target_index)
        self.on_selection_moved()

    def _build_catalog(self) -> None:
        for child in list(self._flow.get_children()):
            self._flow.remove(child)
        for emoji in self._catalog:
            self._flow.add(EmojiCell(emoji))
        self._flow.show_all()

    def _build_recent(self) -> None:
        for child in list(self._recent_flow.get_children()):
            self._recent_flow.remove(child)
        for emoji in self._recent:
            self._recent_flow.add(EmojiCell(emoji))
        self._recent_flow.show_all()

    def _apply_filter(self, query: str) -> None:
        if query.strip():
            matches = search_emojis(self._catalog, query)
            wanted = {emoji.glyph for emoji in matches}
            self._recent_title.hide()
            self._recent_flow.hide()
            visible = self._set_flow_visibility(self._flow, wanted)
        else:
            self._recent_title.set_visible(bool(self._recent))
            self._recent_flow.set_visible(bool(self._recent))
            recent = self._set_flow_visibility(
                self._recent_flow,
                {emoji.glyph for emoji in self._recent},
            )
            all_emojis = self._set_flow_visibility(
                self._flow,
                {emoji.glyph for emoji in self._catalog},
            )
            visible = recent + all_emojis
        self._visible = tuple(visible)
        self.session.set_items(len(visible), reset_selection=True)
        if query.strip():
            self._positions = tuple(
                (index // EMOJI_PICKER_COLUMNS, index % EMOJI_PICKER_COLUMNS)
                for index in range(len(visible))
            )
        else:
            recent_positions = tuple(
                (index // EMOJI_PICKER_COLUMNS, index % EMOJI_PICKER_COLUMNS)
                for index in range(len(recent))
            )
            catalog_row_offset = (len(recent) + EMOJI_PICKER_COLUMNS - 1) // EMOJI_PICKER_COLUMNS
            catalog_positions = tuple(
                (
                    catalog_row_offset + index // EMOJI_PICKER_COLUMNS,
                    index % EMOJI_PICKER_COLUMNS,
                )
                for index in range(len(all_emojis))
            )
            self._positions = recent_positions + catalog_positions
        if visible:
            self.set_empty_visible(False)
            self._flow.unselect_all()
            self._recent_flow.unselect_all()
            first_parent = visible[0].get_parent()
            if isinstance(first_parent, Gtk.FlowBox):
                first_parent.select_child(visible[0])
        else:
            self.set_empty_visible(True)

    @staticmethod
    def _set_flow_visibility(flow: Gtk.FlowBox, wanted: set[str]) -> list[EmojiCell]:
        visible: list[EmojiCell] = []
        for child in flow.get_children():
            if not isinstance(child, EmojiCell):
                continue
            show = child.emoji.glyph in wanted
            child.set_visible(show)
            if show:
                visible.append(child)
        return visible

    @staticmethod
    def _section_title(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.get_style_context().add_class("picker-emoji-section-title")
        return label

    @staticmethod
    def _new_flow(style_class: str) -> Gtk.FlowBox:
        flow = Gtk.FlowBox()
        EmojiPickerWindow._configure_flow(flow, style_class)
        return flow

    @staticmethod
    def _configure_flow(flow: Gtk.FlowBox, style_class: str) -> None:
        flow.get_style_context().add_class(style_class)
        flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        flow.set_activate_on_single_click(True)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(EMOJI_PICKER_COLUMNS)
        flow.set_max_children_per_line(EMOJI_PICKER_COLUMNS)
        flow.set_column_spacing(2)
        flow.set_row_spacing(2)

    def _select_emoji(self, emoji: EmojiRecord) -> None:
        self._on_copy(emoji.glyph)
        self.dismiss_immediately()

    def _on_child_activated(self, _flow: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        if isinstance(child, EmojiCell):
            self._select_emoji(child.emoji)

    def _ensure_child_visible(self, child: Gtk.FlowBoxChild, attempt: int = 0) -> bool:
        origin = origin_in(child, self._content)
        adjustment = self._scrolled.get_vadjustment()
        height = child.get_allocated_height()
        page = 0.0 if adjustment is None else adjustment.get_page_size()
        if origin is None or adjustment is None or height <= 0 or page <= 0:
            if attempt < 2:
                GLib.idle_add(self._ensure_child_visible, child, attempt + 1)
            return False
        _x, y = origin
        target = scroll_to_reveal(adjustment.get_value(), page, y, height)
        if abs(target - adjustment.get_value()) > 0.5:
            adjustment.set_value(target)
        return False


def origin_in(widget: Gtk.Widget, ancestor: Gtk.Widget) -> tuple[int, int] | None:
    """Position of ``widget`` inside ``ancestor``.

    ``translate_coordinates`` returns ``(x, y)`` or ``None``. Unpacking a
    success flag from that pair used to abort the scroll callback.
    """
    translated = widget.translate_coordinates(ancestor, 0, 0)
    if translated is not None:
        return int(translated[0]), int(translated[1])
    x = 0
    y = 0
    current: Gtk.Widget | None = widget
    while current is not None and current is not ancestor:
        allocation = current.get_allocation()
        x += int(allocation.x)
        y += int(allocation.y)
        current = current.get_parent()
    if current is not ancestor:
        return None
    return x, y
