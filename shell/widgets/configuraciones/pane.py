"""Reusable settings navigation/content pane for embedding in other windows."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...settings.manager import SettingsManager
from ...settings.schema import CATEGORY_META, CategoryId
from .pages import build_category_page

SEARCH_DESTINATION = "__search__"
Destination = CategoryId | str


def ordered_categories(manager: SettingsManager) -> tuple[CategoryId, ...]:
    return (CategoryId.GENERAL,) + tuple(
        category
        for category in manager.categories_present()
        if category is not CategoryId.GENERAL
    )


def build_settings_nav(
    manager: SettingsManager,
    *,
    include_search: bool,
    on_select: Callable[[Destination], None],
) -> tuple[Gtk.Widget, dict[Destination, Gtk.Button]]:
    """Build right-rail navigation reusing the settings category order."""
    nav_scroll = Gtk.ScrolledWindow()
    nav_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    nav_scroll.set_vexpand(True)
    nav_scroll.get_style_context().add_class("settings-nav-scroll")
    nav_scroll.get_style_context().add_class("control-center-nav-scroll")

    nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    nav.get_style_context().add_class("settings-nav")
    nav.get_style_context().add_class("control-center-nav")
    nav_scroll.add(nav)

    buttons: dict[Destination, Gtk.Button] = {}

    if include_search:
        search_button = Gtk.Button(label="Buscar", relief=Gtk.ReliefStyle.NONE)
        search_button.get_style_context().add_class("settings-nav-button")
        search_button.set_halign(Gtk.Align.FILL)
        _align_nav_button_label(search_button)
        search_button.connect("clicked", lambda _btn: on_select(SEARCH_DESTINATION))
        nav.pack_start(search_button, False, False, 0)
        buttons[SEARCH_DESTINATION] = search_button

    for category in ordered_categories(manager):
        label, _subtitle = CATEGORY_META[category]
        button = Gtk.Button(label=label, relief=Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("settings-nav-button")
        button.set_halign(Gtk.Align.FILL)
        _align_nav_button_label(button)
        button.connect("clicked", lambda _btn, cat=category: on_select(cat))
        nav.pack_start(button, False, False, 0)
        buttons[category] = button

    return nav_scroll, buttons


def _align_nav_button_label(button: Gtk.Button) -> None:
    child = button.get_child()
    if isinstance(child, Gtk.Label):
        child.set_xalign(0.0)
        child.set_halign(Gtk.Align.START)


def show_settings_category(
    content_host: Gtk.Box,
    manager: SettingsManager,
    category: CategoryId,
    *,
    on_change: Callable[[str, object], None],
) -> Gtk.Widget:
    """Replace content host with one category page."""
    for child in list(content_host.get_children()):
        content_host.remove(child)
        child.destroy()
    page = build_category_page(manager, category, on_change=on_change)
    content_host.pack_start(page, True, True, 0)
    content_host.show_all()
    return page
