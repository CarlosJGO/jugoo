"""Base container for shell modules with shared surface styling."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .tokens import SHELL_MODULE_INNER_SPACING

SHELL_MODULE_CLASS = "shell-module"


class ShellModule(Gtk.Box):
    """Horizontal or vertical module box with the shared shell surface treatment."""

    def __init__(
        self,
        module_class: str,
        *,
        orientation: Gtk.Orientation = Gtk.Orientation.HORIZONTAL,
        spacing: int | None = None,
    ) -> None:
        super().__init__(orientation=orientation)
        style = self.get_style_context()
        style.add_class(SHELL_MODULE_CLASS)
        if module_class:
            style.add_class(module_class)
        self.set_spacing(
            spacing if spacing is not None else SHELL_MODULE_INNER_SPACING
        )
