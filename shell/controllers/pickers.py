"""Coordinates Search, Clipboard, and Emoji picker overlays on the running shell."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ..eventbus import EventBus
from ..popup_handle import PopupHandle
from ..servicios.emojis.catalogo import load_emojis
from ..servicios.portapapeles.servicio import CLIPBOARD_CHANGED, ClipboardService, copy_text
from ..widgets.pickers.clipboard import ClipboardPickerWindow
from ..widgets.pickers.emoji import EmojiPickerWindow


class PickersController:
    """One overlay at a time; Hyprland binds talk to the existing Jugoo instance."""

    def __init__(
        self,
        event_bus: EventBus,
        clipboard: ClipboardService,
        shell_window: Gtk.Window,
        *,
        close_launcher: Callable[[], None],
    ) -> None:
        self._event_bus = event_bus
        self._clipboard = clipboard
        self._close_launcher = close_launcher
        self._emojis = None
        self._clipboard_picker = PopupHandle(
            lambda: ClipboardPickerWindow(
                shell_window,
                on_refresh=lambda: self._clipboard.entries,
                on_copy=self._clipboard.copy_entry,
            )
        )
        self._emoji_picker = PopupHandle(
            lambda: EmojiPickerWindow(
                shell_window,
                on_refresh=self._emoji_catalog,
                on_copy=copy_text,
            )
        )
        event_bus.subscribe(CLIPBOARD_CHANGED, self._on_clipboard_changed)

    def close_pickers(self) -> None:
        clipboard = self._clipboard_picker.maybe
        if clipboard is not None:
            clipboard.close_picker()
        emoji = self._emoji_picker.maybe
        if emoji is not None:
            emoji.close_picker()

    def close(self) -> None:
        self.close_pickers()
        self._event_bus.unsubscribe(CLIPBOARD_CHANGED, self._on_clipboard_changed)

    def toggle_clipboard(self) -> None:
        picker = self._clipboard_picker.get()
        if picker.get_visible():
            picker.close_picker()
            return
        self._close_launcher()
        emoji = self._emoji_picker.maybe
        if emoji is not None:
            emoji.close_picker()
        picker.open_picker()

    def toggle_emoji(self) -> None:
        picker = self._emoji_picker.get()
        if picker.get_visible():
            picker.close_picker()
            return
        self._close_launcher()
        clipboard = self._clipboard_picker.maybe
        if clipboard is not None:
            clipboard.close_picker()
        picker.open_picker()

    def _emoji_catalog(self):
        if self._emojis is None:
            self._emojis = load_emojis()
        return self._emojis

    def _on_clipboard_changed(self, entries: object) -> None:
        picker = self._clipboard_picker.maybe
        if picker is None or not picker.get_visible():
            return
        if isinstance(entries, tuple):
            GLib.idle_add(picker.set_entries, entries)
