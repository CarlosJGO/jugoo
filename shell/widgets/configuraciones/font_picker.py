"""Searchable font family picker with per-row type previews."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Pango

from ...settings.schema import APPLY_LIVE, SettingDef
from ...ui.fonts import SYSTEM_UI_FONT_CHOICE, normalize_ui_font

_POPOVER_MAX_HEIGHT = 280
_POPOVER_MIN_WIDTH = 280
_PREVIEW_SIZE_PT = 12


class FontFamilyPicker(Gtk.Box):
    """Settings control: button + scrolled popover listing fonts in their own face."""

    def __init__(
        self,
        definition: SettingDef,
        value: object,
        *,
        choices: tuple[tuple[str, str], ...],
        on_change: Callable[[str, object], None],
        apply_label: str,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("settings-row")
        self._definition = definition
        self._on_change = on_change
        self._choices = choices
        self._value = normalize_ui_font(value)
        self._rows: list[tuple[str, Gtk.ListBoxRow, str]] = []

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)

        title = Gtk.Label(label=definition.label, xalign=0)
        title.get_style_context().add_class("settings-row-title")
        texts.pack_start(title, False, False, 0)
        if definition.description:
            desc = Gtk.Label(label=definition.description, xalign=0)
            desc.get_style_context().add_class("settings-row-desc")
            desc.set_line_wrap(True)
            desc.set_max_width_chars(48)
            texts.pack_start(desc, False, False, 0)
        header.pack_start(texts, True, True, 0)

        self._button = Gtk.MenuButton()
        self._button.get_style_context().add_class("settings-font-button")
        self._button.set_valign(Gtk.Align.CENTER)
        self._button_label = Gtk.Label(xalign=0)
        self._button_label.get_style_context().add_class("settings-font-button-label")
        self._button_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._button_label.set_max_width_chars(22)
        self._button.add(self._button_label)
        header.pack_end(self._button, False, False, 0)
        self.pack_start(header, False, False, 0)

        if definition.apply != APPLY_LIVE:
            hint = Gtk.Label(label=apply_label, xalign=0)
            hint.get_style_context().add_class("settings-row-hint")
            self.pack_start(hint, False, False, 0)

        self._popover = Gtk.Popover()
        self._popover.get_style_context().add_class("settings-font-popover")
        self._popover.set_relative_to(self._button)
        self._popover.set_position(Gtk.PositionType.BOTTOM)
        self._button.set_popover(self._popover)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.get_style_context().add_class("settings-font-popover-body")
        body.set_size_request(_POPOVER_MIN_WIDTH, -1)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Buscar fuente…")
        self._search.get_style_context().add_class("settings-font-search")
        self._search.connect("search-changed", self._on_search_changed)
        body.pack_start(self._search, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(160)
        scrolled.set_max_content_height(_POPOVER_MAX_HEIGHT)
        scrolled.set_propagate_natural_height(True)
        scrolled.get_style_context().add_class("settings-font-scroll")

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.get_style_context().add_class("settings-font-list")
        self._list.connect("row-activated", self._on_row_activated)
        scrolled.add(self._list)
        body.pack_start(scrolled, True, True, 0)

        self._popover.add(body)
        body.show_all()

        self._rebuild_rows()
        self._sync_button_label()

    def _label_for(self, value: str) -> str:
        wanted = SYSTEM_UI_FONT_CHOICE if not value else value
        for item, label in self._choices:
            if item == wanted:
                return label
        return value or "Sistema (predeterminada)"

    def _sync_button_label(self) -> None:
        label = self._label_for(self._value)
        self._button_label.set_text(label)
        self._button.set_tooltip_text(label)
        if self._value:
            self._apply_preview_font(self._button_label, self._value)
        else:
            self._button_label.override_font(None)

    def _rebuild_rows(self) -> None:
        for child in list(self._list.get_children()):
            self._list.remove(child)
        self._rows.clear()

        selected = SYSTEM_UI_FONT_CHOICE if not self._value else self._value
        active_row: Gtk.ListBoxRow | None = None
        for item, label in self._choices:
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class("settings-font-row")
            text = Gtk.Label(label=label, xalign=0)
            text.get_style_context().add_class("settings-font-preview")
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_max_width_chars(36)
            if item != SYSTEM_UI_FONT_CHOICE:
                self._apply_preview_font(text, item)
            row.add(text)
            row.show_all()
            self._list.add(row)
            self._rows.append((item, row, label.casefold()))
            if item == selected:
                active_row = row

        if active_row is not None:
            self._list.select_row(active_row)

    @staticmethod
    def _apply_preview_font(label: Gtk.Label, family: str) -> None:
        desc = Pango.FontDescription()
        desc.set_family(family)
        desc.set_size(_PREVIEW_SIZE_PT * Pango.SCALE)
        # USER-priority override so global UI font CSS does not mask the preview.
        label.override_font(desc)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        needle = entry.get_text().casefold().strip()
        for _item, row, haystack in self._rows:
            visible = not needle or needle in haystack
            row.set_visible(visible)

    def _on_row_activated(
        self,
        _list: Gtk.ListBox,
        row: Gtk.ListBoxRow,
    ) -> None:
        index = row.get_index()
        if index < 0 or index >= len(self._rows):
            return
        item, _row, _hay = self._rows[index]
        value = normalize_ui_font(item)
        self._value = value
        self._sync_button_label()
        self._popover.popdown()
        self._on_change(self._definition.key, value)
