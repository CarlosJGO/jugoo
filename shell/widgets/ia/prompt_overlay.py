"""Centered AI prompt entry anchored just under the top bar."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ...config import AI_PROMPT_TOP_MARGIN, AI_PROMPT_WIDTH
from ...identity import TITLE_AI_PROMPT
from ...popup_handle import hide_popup, present_popup
from ...ui.starfield import install_starfield, resolve_event_bus
from ...window_identity import (
    configure_interactive_popup,
    configure_toplevel,
    register_shell_popup,
)


class AiPromptOverlay(Gtk.Window):
    """Single-line prompt layer: type, Enter submits, Escape cancels."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        *,
        on_submit: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._closing = False

        self.set_name("shell-ai-prompt")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_AI_PROMPT)
        configure_interactive_popup(self)
        self.set_default_size(AI_PROMPT_WIDTH, -1)
        self._configure_layer_shell()

        aligner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        aligner.set_halign(Gtk.Align.CENTER)
        aligner.set_valign(Gtk.Align.START)
        self.add(aligner)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.get_style_context().add_class("ai-prompt-card")
        card.set_size_request(AI_PROMPT_WIDTH, -1)
        install_starfield(
            aligner,
            card,
            resolve_event_bus(shell_window),
        )

        icon = Gtk.Image.new_from_icon_name("system-search-symbolic", Gtk.IconSize.MENU)
        icon.set_pixel_size(16)
        icon.get_style_context().add_class("ai-prompt-icon")
        card.pack_start(icon, False, False, 0)

        self._entry = Gtk.Entry()
        self._entry.get_style_context().add_class("ai-prompt-entry")
        self._entry.set_placeholder_text("Pregúntale a Jugoo…  (/new para reiniciar)")
        self._entry.set_hexpand(True)
        self._entry.connect("activate", self._on_activate)
        card.pack_start(self._entry, True, True, 0)

        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("map", self._on_map)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-ai-prompt")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, AI_PROMPT_TOP_MARGIN)

    def is_open(self) -> bool:
        return bool(self.get_mapped() and self.get_visible() and not self._closing)

    def open_prompt(self) -> None:
        self._closing = False
        self._entry.set_text("")
        present_popup(self)
        self.show_all()
        GLib.idle_add(self._focus_entry)

    def close_prompt(self) -> None:
        if self._closing or not self.get_visible():
            return
        self._closing = True
        hide_popup(self)
        self._closing = False

    def _focus_entry(self) -> bool:
        self.present_with_time(Gtk.get_current_event_time())
        self._entry.grab_focus()
        return False

    def _on_map(self, *_args) -> None:
        GLib.idle_add(self._focus_entry)

    def _on_activate(self, *_args) -> None:
        text = self._entry.get_text().strip()
        self.close_prompt()
        if text:
            self._on_submit(text)

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key = Gdk.keyval_name(event.keyval) or ""
        if key in {"Escape", "ISO_Left_Tab"} and (event.state & Gdk.ModifierType.SHIFT_MASK) == 0:
            self.close_prompt()
            if self._on_cancel is not None:
                self._on_cancel()
            return True
        return False
