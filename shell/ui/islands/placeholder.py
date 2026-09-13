"""Geometry twin left in the bar while a module is detached."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk


class IslandPlaceholder(Gtk.EventBox):
    """Same footprint as the detached host, visually attenuated."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self.get_style_context().add_class("island-placeholder")
        self.set_size_request(max(1, int(width)), max(1, int(height)))
        self.set_visible_window(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        inner.get_style_context().add_class("island-placeholder-inner")
        self.add(inner)
        self.show_all()
