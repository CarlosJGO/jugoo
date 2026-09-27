"""Shared GTK context menus for dock and launcher application rows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ... import config as shell_config
from ...identity import TITLE_APP_MENU
from ...popup_handle import (
    PopupOutsideDismiss,
    hide_popup,
    present_popup,
    register_owned_surface,
    unregister_owned_surface,
)
from ...window_identity import (
    configure_passive_popup,
    configure_toplevel,
    register_shell_popup,
)

MenuEntry = tuple[str, Callable[[], None]] | None


@dataclass(frozen=True)
class MenuHeader:
    """Non-interactive section label inside a dock context menu."""

    label: str


@dataclass(frozen=True)
class AppTargetEntry:
    """Single swap/promote target: icon + name (no duplicated source app)."""

    name: str
    icon: str
    callback: Callable[[], None]


DockMenuEntry = MenuEntry | MenuHeader | AppTargetEntry

_TARGET_ICON_PX = 18

# Keep one live dock menu so repeated right-clicks replace instead of stacking.
_active_dock_menu: "_DockAppMenu" | None = None


class _DockAppMenu(Gtk.Window):
    """Scrollable dock context menu (Gtk.Menu cannot cap height on Wayland)."""

    def __init__(self, parent: Gtk.Window | None) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._dismiss = PopupOutsideDismiss()
        self._on_deactivate: Callable[[], None] | None = None
        self._owner_popup: Gtk.Window | None = None
        self.set_name("shell-app-menu-window")
        if parent is not None:
            register_shell_popup(self, parent)
        configure_toplevel(self, title=TITLE_APP_MENU)
        configure_passive_popup(self)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("shell-app-menu-panel")

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._apply_max_height()
        self._scrolled.set_propagate_natural_height(True)
        self._scrolled.set_propagate_natural_width(True)
        self._scrolled.get_style_context().add_class("shell-app-menu-scroll")

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._box.get_style_context().add_class("shell-app-menu")
        self._scrolled.add(self._box)
        outer.pack_start(self._scrolled, True, True, 0)
        self.add(outer)

    def open(
        self,
        event: Gdk.EventButton,
        entries: Sequence[DockMenuEntry],
        *,
        attach_widget: Gtk.Widget | None,
        owner_popup: Gtk.Window | None,
        on_deactivate: Callable[[], None] | None,
    ) -> None:
        self._on_deactivate = on_deactivate
        self._owner_popup = owner_popup
        self._apply_max_height()
        self._rebuild(entries)

        if owner_popup is not None:
            register_owned_surface(owner_popup, self)

        anchors: tuple[Gtk.Widget, ...] = ()
        shell = None
        if attach_widget is not None:
            anchors = (attach_widget,)
            toplevel = attach_widget.get_toplevel()
            if isinstance(toplevel, Gtk.Window):
                shell = toplevel
        if shell is None and owner_popup is not None:
            shell = owner_popup

        present_popup(self)
        GLib.idle_add(self._place_at_pointer, float(event.x_root), float(event.y_root))

        if shell is not None:
            self._dismiss.install(
                self,
                shell,
                anchors,
                self.close_menu,
                extra_windows=(owner_popup,) if owner_popup is not None else (),
            )

    def _apply_max_height(self) -> None:
        height = max(160, int(shell_config.PINNED_APP_MENU_MAX_HEIGHT))
        self._scrolled.set_max_content_height(height)

    def _place_at_pointer(self, x: float, y: float) -> bool:
        if not self.get_visible():
            return False
        self.set_size_request(-1, -1)
        self.resize(1, 1)
        # Let natural size resolve, then move under the cursor.
        GLib.idle_add(self._move_to, int(x), int(y))
        return False

    def _move_to(self, x: int, y: int) -> bool:
        if not self.get_visible():
            return False
        from ...window_identity import apply_popup_position

        apply_popup_position(self, title=TITLE_APP_MENU, x=x, y=y)
        return False

    def _rebuild(self, entries: Sequence[DockMenuEntry]) -> None:
        for child in list(self._box.get_children()):
            self._box.remove(child)
        for entry in entries:
            if entry is None:
                separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                self._box.pack_start(separator, False, False, 2)
                continue
            if isinstance(entry, MenuHeader):
                label = Gtk.Label(label=entry.label, xalign=0)
                label.get_style_context().add_class("shell-app-menu-header")
                label.set_sensitive(False)
                self._box.pack_start(label, False, False, 0)
                continue
            if isinstance(entry, AppTargetEntry):
                self._box.pack_start(_target_button(entry, self._picked), False, False, 0)
                continue
            label, callback = entry
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("shell-app-menu-item")
            button.set_halign(Gtk.Align.FILL)
            child = button.get_child()
            if isinstance(child, Gtk.Label):
                child.set_xalign(0)
            button.connect("clicked", self._picked, callback)
            self._box.pack_start(button, False, False, 0)
        self._box.show_all()
        self.show_all()

    def _picked(self, _button: Gtk.Widget, callback: Callable[[], None]) -> None:
        self.close_menu()
        callback()

    def close_menu(self) -> None:
        global _active_dock_menu
        self._dismiss.uninstall()
        if self._owner_popup is not None:
            unregister_owned_surface(self._owner_popup, self)
            self._owner_popup = None
        hide_popup(self)
        callback = self._on_deactivate
        self._on_deactivate = None
        if _active_dock_menu is self:
            _active_dock_menu = None
        if callback is not None:
            callback()


def popup_application_menu(
    event: Gdk.EventButton,
    entries: Sequence[DockMenuEntry],
    *,
    attach_widget: Gtk.Widget | None = None,
    owner_popup: Gtk.Window | None = None,
    on_deactivate: Callable[[], None] | None = None,
) -> None:
    """Show a scroll-capped dock menu near the pointer."""
    global _active_dock_menu
    if _active_dock_menu is not None:
        previous = _active_dock_menu
        _active_dock_menu = None
        previous.close_menu()

    parent = None
    if attach_widget is not None:
        toplevel = attach_widget.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            parent = toplevel
    if parent is None and owner_popup is not None:
        parent = owner_popup

    menu = _DockAppMenu(parent)
    _active_dock_menu = menu
    menu.open(
        event,
        entries,
        attach_widget=attach_widget,
        owner_popup=owner_popup,
        on_deactivate=on_deactivate,
    )


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


def _target_button(entry: AppTargetEntry, on_click) -> Gtk.Button:
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("shell-app-menu-item")
    button.get_style_context().add_class("shell-app-menu-target")
    button.set_halign(Gtk.Align.FILL)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    image = Gtk.Image.new_from_icon_name(
        entry.icon or "application-x-executable",
        Gtk.IconSize.MENU,
    )
    image.set_pixel_size(_TARGET_ICON_PX)
    label = Gtk.Label(label=entry.name)
    label.set_xalign(0)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_max_width_chars(22)
    row.pack_start(image, False, False, 0)
    row.pack_start(label, True, True, 0)
    button.add(row)
    button.connect("clicked", on_click, entry.callback)
    return button


def _picked(
    _button: Gtk.Button,
    on_picked: Callable[[Callable[[], None]], None],
    callback: Callable[[], None],
) -> None:
    on_picked(callback)
