"""Placeholder sections reserved for upcoming control center modules."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...ui import ShellModule


class ControlCenterPlaceholderSection(ShellModule):
    """Reserved slot for a future control center module."""

    def __init__(self, title: str, *, module_class: str) -> None:
        super().__init__(module_class, spacing=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("control-center-section")
        self.pack_start(box, True, True, 0)

        heading = Gtk.Label(xalign=0)
        heading.get_style_context().add_class("control-center-section-title")
        heading.set_markup(f"<b>{title}</b>")
        box.pack_start(heading, False, False, 0)

        hint = Gtk.Label(label="Próximamente", xalign=0)
        hint.get_style_context().add_class("control-center-placeholder")
        box.pack_start(hint, False, False, 0)
