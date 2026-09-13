"""Floating stage hosted on the bar Gtk.Overlay via get-child-position."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk


class IslandStage:
    """Positions detached Organic Island hosts on the shell overlay."""

    def __init__(self) -> None:
        self._overlay: Gtk.Overlay | None = None
        self._positions: dict[Gtk.Widget, tuple[int, int]] = {}
        self._position_handler_id = 0

    def mount_on(self, overlay: Gtk.Overlay) -> None:
        self._overlay = overlay
        if self._position_handler_id:
            overlay.disconnect(self._position_handler_id)
        self._position_handler_id = overlay.connect(
            "get-child-position",
            self._on_get_child_position,
        )

    def place(self, widget: Gtk.Widget, x: int, y: int) -> None:
        if self._overlay is None:
            return
        self._positions[widget] = (int(x), int(y))
        parent = widget.get_parent()
        if parent is self._overlay:
            self._overlay.queue_resize()
            widget.show_all()
            return
        if parent is not None:
            parent.remove(widget)
        self._overlay.add_overlay(widget)
        self._overlay.set_overlay_pass_through(widget, False)
        widget.show_all()
        self._overlay.queue_resize()

    def move(self, widget: Gtk.Widget, x: int, y: int) -> None:
        self._positions[widget] = (int(x), int(y))
        if self._overlay is not None:
            self._overlay.queue_resize()

    def release(self, widget: Gtk.Widget) -> None:
        self._positions.pop(widget, None)
        if self._overlay is None:
            return
        if widget.get_parent() is self._overlay:
            self._overlay.remove(widget)

    def clear_if_empty(self) -> None:
        return

    def _on_get_child_position(
        self,
        _overlay: Gtk.Overlay,
        widget: Gtk.Widget,
        allocation,
    ) -> bool:
        pos = self._positions.get(widget)
        if pos is None:
            return False
        x, y = pos
        req = widget.get_preferred_size()[1]  # natural
        width = max(widget.get_allocated_width(), req.width, 1)
        height = max(widget.get_allocated_height(), req.height, 1)
        # Prefer explicit size-request when animating.
        min_req = widget.get_preferred_size()[0]
        width = max(width, min_req.width, 1)
        height = max(height, min_req.height, 1)
        allocation.x = int(x)
        allocation.y = int(y)
        allocation.width = int(width)
        allocation.height = int(height)
        return True
