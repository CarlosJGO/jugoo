"""Interactive, row-anchored context popover for launcher applications."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, Pango

from ...servicios.aplicaciones.inspection import ApplicationInspection

MenuEntry = tuple[str, Callable[[], None]] | None


@dataclass(frozen=True)
class PopoverPageEntry:
    """An action that replaces the popover contents without closing it."""

    label: str
    callback: Callable[[], Gtk.Widget]


LauncherMenuEntry = MenuEntry | PopoverPageEntry


class LauncherContextPopover(Gtk.Popover):
    """GTK-native context menu whose input and placement belong to its row."""

    def __init__(
        self,
        anchor: Gtk.Widget,
        entries: Sequence[LauncherMenuEntry],
        on_closed: Callable[["LauncherContextPopover"], None],
    ) -> None:
        super().__init__()
        self.set_relative_to(anchor)
        self.set_position(Gtk.PositionType.RIGHT)
        self.set_modal(True)
        self.set_transitions_enabled(False)
        self.get_style_context().add_class("launcher-context-popover")
        self._on_closed = on_closed
        self.connect("closed", self._handle_closed)
        self.connect("key-press-event", self._on_key_press)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        actions.get_style_context().add_class("launcher-action-menu")
        self._stack.add_named(actions, "actions")
        self._build_actions(actions, entries)
        self.add(self._stack)
        self.show_all()

    @property
    def showing_information(self) -> bool:
        return self._stack.get_visible_child_name() == "information"

    def popup(self) -> None:
        Gtk.Popover.popup(self)

    def popdown(self) -> None:
        Gtk.Popover.popdown(self)

    def show_actions(self) -> None:
        self._stack.set_visible_child_name("actions")
        self.queue_resize()

    def show_information(self, content: Gtk.Widget) -> None:
        previous = self._stack.get_child_by_name("information")
        if previous is not None:
            self._stack.remove(previous)
        self._stack.add_named(content, "information")
        self._stack.set_visible_child_name("information")
        self.queue_resize()

    def _build_actions(
        self,
        container: Gtk.Box,
        entries: Sequence[LauncherMenuEntry],
    ) -> None:
        for entry in entries:
            if entry is None:
                container.pack_start(
                    Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                    False,
                    False,
                    2,
                )
                continue
            if isinstance(entry, PopoverPageEntry):
                button = self._button(entry.label)
                button.connect("clicked", self._picked_page, entry.callback)
            else:
                label, callback = entry
                button = self._button(label)
                button.connect("clicked", self._picked, callback)
            container.pack_start(button, False, False, 0)

    @staticmethod
    def _button(label: str) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("shell-app-menu-item")
        button.set_halign(Gtk.Align.FILL)
        child = button.get_child()
        if isinstance(child, Gtk.Label):
            child.set_xalign(0)
        return button

    def _picked(self, _button: Gtk.Button, callback: Callable[[], None]) -> None:
        self.popdown()
        callback()

    def _picked_page(
        self,
        _button: Gtk.Button,
        callback: Callable[[], Gtk.Widget],
    ) -> None:
        self.show_information(callback())

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval != Gdk.KEY_Escape:
            return False
        if self.showing_information:
            self.show_actions()
        else:
            self.popdown()
        return True

    def _handle_closed(self, *_args) -> None:
        self._on_closed(self)


def build_application_info(
    details: ApplicationInspection,
    on_back: Callable[[], None],
) -> Gtk.Widget:
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_propagate_natural_height(True)
    scroll.set_max_content_height(330)
    scroll.get_style_context().add_class("application-info-scroll")
    scroll.add(body)

    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    page.get_style_context().add_class("application-info-popup")
    page.get_style_context().add_class("launcher-action-menu")
    page.set_size_request(360, -1)
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = Gtk.Label(label="Información", xalign=0)
    title.set_hexpand(True)
    title.get_style_context().add_class("application-info-title")
    header.pack_start(title, True, True, 0)
    back = Gtk.Button(label="← Atrás", relief=Gtk.ReliefStyle.NONE)
    back.get_style_context().add_class("application-info-back")
    back.connect("clicked", lambda *_args: on_back())
    header.pack_end(back, False, False, 0)
    page.pack_start(header, False, False, 0)
    page.pack_start(scroll, True, True, 0)

    _add_info_field(body, "Nombre", details.name)
    _add_info_field(body, "Origen", details.origin)
    if details.package:
        _add_info_field(body, "ID / paquete", details.package)
    for label, value in details.locations:
        _add_info_field(body, label, value)
    if details.desktop_exec:
        _add_info_field(body, "Exec del .desktop", details.desktop_exec, monospace=True)
    if details.launch_command:
        _add_info_field(body, "Comando para ejecutar", details.launch_command, monospace=True)
    page.show_all()
    return page


def _add_info_field(
    container: Gtk.Box,
    label: str,
    value: str,
    *,
    monospace: bool = False,
) -> None:
    field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    field.get_style_context().add_class("application-info-field")
    title = Gtk.Label(label=label, xalign=0)
    title.get_style_context().add_class("application-info-label")
    field.pack_start(title, False, False, 0)
    text = Gtk.Label(label=value, xalign=0)
    text.set_line_wrap(True)
    text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    text.set_selectable(True)
    text.get_style_context().add_class(
        "application-info-command" if monospace else "application-info-value"
    )
    field.pack_start(text, False, False, 0)
    container.pack_start(field, False, False, 0)