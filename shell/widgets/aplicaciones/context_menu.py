"""Shared GTK context menus for dock and launcher application rows."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, Gtk

MenuEntry = tuple[str, Callable[[], None]] | None


def popup_application_menu(event: Gdk.EventButton, entries: Sequence[MenuEntry]) -> None:
    """Dock menus can use a real Gtk.Menu; the bar is not an exclusive overlay."""
    menu = Gtk.Menu()
    menu.get_style_context().add_class("shell-app-menu")
    for entry in entries:
        if entry is None:
            menu.append(Gtk.SeparatorMenuItem())
            continue
        label, callback = entry
        item = Gtk.MenuItem(label=label)
        item.connect("activate", _activate, callback)
        menu.append(item)
    menu.show_all()
    menu.popup_at_pointer(event)


def fill_application_menu(
    container: Gtk.Box,
    entries: Sequence[MenuEntry],
    on_picked: Callable[[Callable[[], None]], None],
) -> None:
    """Build an in-window menu so the launcher overlay can keep exclusive keyboard."""
    for child in list(container.get_children()):
        container.remove(child)
    for entry in entries:
        if entry is None:
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            container.pack_start(separator, False, False, 2)
            continue
        label, callback = entry
        button = Gtk.Button(label=label)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("shell-app-menu-item")
        button.set_halign(Gtk.Align.FILL)
        child = button.get_child()
        if isinstance(child, Gtk.Label):
            child.set_xalign(0)
        button.connect("clicked", _picked, on_picked, callback)
        container.pack_start(button, False, False, 0)
    container.show_all()


def _activate(_item: Gtk.MenuItem, callback: Callable[[], None]) -> None:
    callback()


def _picked(
    _button: Gtk.Button,
    on_picked: Callable[[Callable[[], None]], None],
    callback: Callable[[], None],
) -> None:
    on_picked(callback)
