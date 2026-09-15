"""Bar button that opens the Jugoo Settings Center."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...ui import ShellModule
from ..configuraciones.black_hole_icon import BlackHoleIcon

# Match the visual weight of other bar action icons (bell/tasks/power block).
_SETTINGS_ICON_SIZE = 22


class SettingsWidget(ShellModule):
    """Black-hole entry point for Configuraciones — the glyph fills the block."""

    def __init__(self, on_toggle) -> None:
        super().__init__("settings-widget", spacing=0)
        self._on_toggle = on_toggle

        self._button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._button.get_style_context().add_class("settings-button")
        self._button.set_tooltip_text("Configuraciones")
        self._icon = BlackHoleIcon(_SETTINGS_ICON_SIZE)
        self._button.add(self._icon)
        self._button.connect("clicked", self._on_clicked)
        self.pack_start(self._button, False, False, 0)

    def _on_clicked(self, *_args) -> None:
        self._on_toggle()
