"""Coordinates Search, Clipboard, and Emoji picker overlays on the running shell."""

from __future__ import annotations

from collections.abc import Callable
import threading

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ..eventbus import EventBus
from ..popup_handle import PopupHandle
from ..servicios.emojis.catalogo import load_emojis
from ..servicios.escritorio.hyprland import HyprlandService
from ..servicios.portapapeles.servicio import (
    CLIPBOARD_CHANGED,
    ClipboardService,
    copy_text,
    paste_text_to_window,
)
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
        hyprland: HyprlandService,
    ) -> None:
        self._event_bus = event_bus
        self._clipboard = clipboard
        self._hyprland = hyprland
        self._close_launcher = close_launcher
        self._emojis = None
        self._paste_target = ""
        self._clipboard_picker = PopupHandle(
            lambda: ClipboardPickerWindow(
                shell_window,
                on_refresh=lambda: self._clipboard.entries,
                on_copy=self._select_clipboard,
            )
        )
        self._emoji_picker = PopupHandle(
            lambda: EmojiPickerWindow(
                shell_window,
                on_refresh=self._emoji_catalog,
                on_copy=self._select_emoji,
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
        self._remember_paste_target()
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
        self._remember_paste_target()
        clipboard = self._clipboard_picker.maybe
        if clipboard is not None:
            clipboard.close_picker()
        picker.open_picker()

    def _emoji_catalog(self):
        if self._emojis is None:
            self._emojis = load_emojis()
        return self._emojis

    def _remember_paste_target(self) -> None:
        snapshot = self._hyprland.snapshot
        self._paste_target = snapshot.active_window.address if snapshot is not None else ""

    def _select_clipboard(self, entry_id: str) -> None:
        entry = self._clipboard.entry_by_id(entry_id)
        if entry is None:
            return
        self._copy_and_paste(entry.text, lambda: self._clipboard.remember_text(entry.text))

    def _select_emoji(self, text: str) -> None:
        self._copy_and_paste(text)

    def _copy_and_paste(self, text: str, after_copy: Callable[[], object] | None = None) -> None:
        target = self._paste_target

        def worker() -> None:
            if not copy_text(text):
                return
            if after_copy is not None:
                after_copy()
            paste_text_to_window(target)

        threading.Thread(target=worker, name="picker-copy-paste", daemon=True).start()

    def _on_clipboard_changed(self, entries: object) -> None:
        picker = self._clipboard_picker.maybe
        if picker is None or not picker.get_visible():
            return
        if isinstance(entries, tuple):
            GLib.idle_add(picker.set_entries, entries)
