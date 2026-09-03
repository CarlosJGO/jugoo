"""GTK layout primitives for placing shell modules in named regions."""

from __future__ import annotations

from typing import Iterator

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .ui.tokens import SHELL_BAR_MODULE_SPACING, SHELL_BAR_REGION_SPACING

SHELL_BAR_CLASS = "shell-bar"


class ShellRegion(Gtk.Box):
    """A region owns its module ordering, so callers never need GTK packing APIs."""

    def __init__(self, name: str) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_name(name)
        self.set_spacing(SHELL_BAR_MODULE_SPACING)

    def add(self, widget: Gtk.Widget) -> None:
        """Append a module using the region's standard natural-size packing."""
        self.pack_start(widget, False, False, 0)

    def remove(self, widget: Gtk.Widget) -> None:
        """Detach a module that belongs to this region."""
        Gtk.Box.remove(self, widget)

    def __iter__(self) -> Iterator[Gtk.Widget]:
        return iter(self.get_children())


class LeftContainer(ShellRegion):
    def __init__(self) -> None:
        super().__init__("shell-left")


class CenterContainer(ShellRegion):
    def __init__(self) -> None:
        super().__init__("shell-center")


class RightContainer(ShellRegion):
    def __init__(self) -> None:
        super().__init__("shell-right")


def _flex_slot() -> Gtk.Box:
    """Empty expanding slot so left/right can pin to the screen edges."""
    slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    slot.set_hexpand(True)
    slot.set_halign(Gtk.Align.FILL)
    return slot


class ShellLayout(Gtk.Box):
    """Root bar container: one outer capsule wrapping left/center/right regions."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.get_style_context().add_class(SHELL_BAR_CLASS)
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_spacing(SHELL_BAR_REGION_SPACING)
        self.left = LeftContainer()
        self.center = CenterContainer()
        self.right = RightContainer()

        # Pin edges with flex slots. Giving the center region expand=True does
        # not consume leftover width here: CenterContainer reports no hexpand
        # (its child is packed non-expanding), so GTK treats the three regions
        # as a centered group and leaves a large gutter on the left.
        self.left.set_hexpand(False)
        self.left.set_halign(Gtk.Align.START)
        self.center.set_hexpand(False)
        self.center.set_halign(Gtk.Align.CENTER)
        self.right.set_hexpand(False)
        self.right.set_halign(Gtk.Align.END)

        self.pack_start(self.left, False, False, 0)
        self.pack_start(_flex_slot(), True, True, 0)
        self.pack_start(self.center, False, False, 0)
        self.pack_start(_flex_slot(), True, True, 0)
        self.pack_start(self.right, False, False, 0)
