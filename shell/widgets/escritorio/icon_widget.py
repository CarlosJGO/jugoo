"""Single desktop icon: glyph + label, click-to-open / drag."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, Pango

from ...config import (
    DESKTOP_ICON_CELL_HEIGHT,
    DESKTOP_ICON_CELL_WIDTH,
    DESKTOP_ICON_DRAG_THRESHOLD_PX,
    DESKTOP_ICON_LABEL_WIDTH,
    DESKTOP_ICON_SIZE,
)
from ...icons import FALLBACK_ICON
from ...servicios.escritorio.desktop_icons.model import DesktopShortcut

OnActivate = Callable[[str], None]
OnSelect = Callable[[str], None]
OnMoveEnd = Callable[[str, int, int], None]

# Common FreeDesktop name mismatches on Debian/derivatives.
_ICON_ALIASES = {
    "firefox": "firefox-esr",
}


class DesktopIconWidget(Gtk.EventBox):
    """One shortcut on the desktop surface."""

    def __init__(
        self,
        shortcut: DesktopShortcut,
        *,
        on_activate: OnActivate,
        on_select: OnSelect,
        on_move_end: OnMoveEnd,
        selected: bool = False,
    ) -> None:
        super().__init__()
        self.shortcut_id = shortcut.id
        self._shortcut = shortcut
        self._on_activate = on_activate
        self._on_select = on_select
        self._on_move_end = on_move_end
        self._dragging = False
        self._press_root: tuple[float, float] | None = None
        self._origin: tuple[int, int] = (shortcut.x, shortcut.y)
        self._current: tuple[int, int] = (shortcut.x, shortcut.y)

        self.set_visible_window(True)
        self.set_can_focus(True)
        self.set_size_request(DESKTOP_ICON_CELL_WIDTH, DESKTOP_ICON_CELL_HEIGHT)
        self.get_style_context().add_class("desktop-icon")
        if selected:
            self.get_style_context().add_class("selected")

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        column.set_halign(Gtk.Align.CENTER)
        column.set_valign(Gtk.Align.START)

        icon_name = _resolve_icon_name(shortcut.icon, shortcut.type)
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        image.set_pixel_size(DESKTOP_ICON_SIZE)
        image.get_style_context().add_class("desktop-icon-image")
        column.pack_start(image, False, False, 0)

        label = Gtk.Label(label=shortcut.name)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_lines(2)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_max_width_chars(12)
        label.set_width_chars(10)
        label.set_size_request(DESKTOP_ICON_LABEL_WIDTH, -1)
        label.get_style_context().add_class("desktop-icon-label")
        column.pack_start(label, False, False, 0)

        self.add(column)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("key-press-event", self._on_key_press)
        self.show_all()

    @property
    def is_dragging(self) -> bool:
        return self._press_root is not None and self._dragging

    @property
    def position(self) -> tuple[int, int]:
        return self._current

    def set_selected(self, selected: bool) -> None:
        context = self.get_style_context()
        if selected:
            context.add_class("selected")
        else:
            context.remove_class("selected")

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        # Ignore synthetic double-press; open happens on release (Wayland-safe).
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            return True
        self._on_select(self.shortcut_id)
        self._press_root = (float(event.x_root), float(event.y_root))
        self._origin = self._current
        self._dragging = False
        return True

    def _on_motion(self, _widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._press_root is None:
            return False
        dx = float(event.x_root) - self._press_root[0]
        dy = float(event.y_root) - self._press_root[1]
        if not self._dragging:
            if (dx * dx + dy * dy) < (DESKTOP_ICON_DRAG_THRESHOLD_PX ** 2):
                return False
            self._dragging = True
        self._current = (
            max(0, int(self._origin[0] + dx)),
            max(0, int(self._origin[1] + dy)),
        )
        parent = self.get_parent()
        if isinstance(parent, Gtk.Fixed):
            parent.move(self, self._current[0], self._current[1])
        return True

    def _on_button_release(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        if self._press_root is None:
            return False
        if self._dragging:
            self._on_move_end(self.shortcut_id, self._current[0], self._current[1])
        else:
            # Single click (no drag): open. Double-click is unreliable on GTK3/Wayland.
            self._on_activate(self.shortcut_id)
        self._press_root = None
        self._dragging = False
        return True

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        keyval = event.keyval
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._on_activate(self.shortcut_id)
            return True
        return False


def _resolve_icon_name(icon: str, kind: str) -> str:
    candidate = icon.strip() or _default_icon(kind)
    theme = Gtk.IconTheme.get_default()
    for name in (candidate, _ICON_ALIASES.get(candidate.casefold(), ""), FALLBACK_ICON):
        if not name:
            continue
        if theme.lookup_icon(name, DESKTOP_ICON_SIZE, 0) is not None:
            return name
    return FALLBACK_ICON


def _default_icon(kind: str) -> str:
    if kind == "directory":
        return "folder"
    if kind == "file":
        return "text-x-generic"
    if kind == "action":
        return "preferences-system"
    return FALLBACK_ICON
