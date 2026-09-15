"""Reparent a bar module onto a Gtk.Fixed and put it back.

Reserved infrastructure for a future feature other than Focus Mode. Jugoo does
not instantiate this on startup and does not bind any key or listener to it.
The same widget stays alive: input, signals, and internal state survive the
round-trip through independent (x, y) placement.

A future caller creates a Gtk.Fixed, constructs ModuleStage, then calls
detach / animate_to / restore. Nothing here starts a timer or watches windows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .ui.door import _animation_duration_ms, _ease
from .ui.theme import active_theme


@dataclass(frozen=True)
class BarHome:
    """Original pack index of a module inside its BarLayout Gtk.Box."""

    parent: Gtk.Box
    index: int


def capture_bar_home(widget: Gtk.Widget) -> BarHome | None:
    """Record where ``widget`` sits in a Gtk.Box, or None if it is not packed there."""
    parent = widget.get_parent()
    if not isinstance(parent, Gtk.Box):
        return None
    children = parent.get_children()
    try:
        return BarHome(parent=parent, index=children.index(widget))
    except ValueError:
        return None


class ModuleStage:
    """Float live bar modules onto a Gtk.Fixed without destroying them.

    Not Focus Mode. No EventBus, Hyprland watcher, or content heuristic.
    """

    def __init__(self, stage: Gtk.Fixed) -> None:
        self._stage = stage
        self._homes: dict[int, BarHome] = {}
        self._detached: dict[int, Gtk.Widget] = {}
        self._tick_id = 0
        self._anim_from: dict[int, tuple[int, int]] = {}
        self._anim_to: dict[int, tuple[int, int]] = {}
        self._anim_widgets: dict[int, Gtk.Widget] = {}
        self._anim_started_us = 0
        self._anim_duration_ms = 0
        self._on_anim_done: Callable[[], None] | None = None

    def remember_home(self, widget: Gtk.Widget) -> BarHome | None:
        home = capture_bar_home(widget)
        if home is not None:
            self._homes[id(widget)] = home
        return home

    def detach(self, widget: Gtk.Widget, x: int, y: int) -> None:
        """Take ``widget`` out of BarLayout and place it on the Fixed at ``(x, y)``."""
        if id(widget) not in self._homes:
            self.remember_home(widget)
        parent = widget.get_parent()
        if parent is not None:
            parent.remove(widget)
        self._stage.put(widget, int(x), int(y))
        self._detached[id(widget)] = widget
        widget.show()

    def move(self, widget: Gtk.Widget, x: int, y: int) -> None:
        if widget.get_parent() is self._stage:
            self._stage.move(widget, int(x), int(y))

    def animate_to(
        self,
        targets: Mapping[Gtk.Widget, tuple[int, int]],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Animate detached widgets to arbitrary coordinates using the door ease/tick."""
        self._cancel_tick()
        self._anim_from.clear()
        self._anim_to.clear()
        self._anim_widgets.clear()
        for widget, dest in targets.items():
            key = id(widget)
            self._anim_widgets[key] = widget
            self._anim_from[key] = self._stage_xy(widget)
            self._anim_to[key] = (int(dest[0]), int(dest[1]))

        done = on_done or (lambda: None)
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
            self._snap_to(self._anim_to)
            done()
            return
        duration = _animation_duration_ms()
        if duration <= 0:
            self._snap_to(self._anim_to)
            done()
            return
        self._anim_duration_ms = duration
        self._anim_started_us = GLib.get_monotonic_time()
        self._on_anim_done = done
        self._tick_id = self._stage.add_tick_callback(self._on_tick)

    def restore(self, widget: Gtk.Widget) -> None:
        """Reinsert ``widget`` at its remembered bar_position in the original Gtk.Box."""
        home = self._homes.get(id(widget))
        if home is None:
            home = capture_bar_home(widget)
        parent = widget.get_parent()
        if parent is self._stage:
            self._stage.remove(widget)
        elif parent is not None and (home is None or parent is not home.parent):
            parent.remove(widget)
        if widget.get_parent() is None and home is not None:
            home.parent.pack_start(widget, False, False, 0)
            home.parent.reorder_child(widget, home.index)
        self._detached.pop(id(widget), None)
        widget.show()

    def restore_all(self) -> None:
        ordered = sorted(
            self._detached.values(),
            key=lambda item: (
                id(self._homes[id(item)].parent) if id(item) in self._homes else 0,
                self._homes[id(item)].index if id(item) in self._homes else 0,
            ),
        )
        for widget in list(ordered):
            self.restore(widget)

    def close(self) -> None:
        self._cancel_tick()

    def _stage_xy(self, widget: Gtk.Widget) -> tuple[int, int]:
        allocation = widget.get_allocation()
        origin = widget.translate_coordinates(self._stage, 0, 0)
        if origin is None:
            return int(allocation.x), int(allocation.y)
        return int(origin[0]), int(origin[1])

    def _snap_to(self, targets: Mapping[int, tuple[int, int]]) -> None:
        for key, dest in targets.items():
            widget = self._anim_widgets.get(key)
            if widget is None or widget.get_parent() is not self._stage:
                continue
            self._stage.move(widget, dest[0], dest[1])

    def _on_tick(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        elapsed_ms = (GLib.get_monotonic_time() - self._anim_started_us) / 1000.0
        t = min(1.0, elapsed_ms / max(1, self._anim_duration_ms))
        eased = _ease(t)
        for key, widget in self._anim_widgets.items():
            start = self._anim_from.get(key)
            dest = self._anim_to.get(key)
            if start is None or dest is None:
                continue
            x = int(round(start[0] + (dest[0] - start[0]) * eased))
            y = int(round(start[1] + (dest[1] - start[1]) * eased))
            if widget.get_parent() is self._stage:
                self._stage.move(widget, x, y)
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        callback = self._on_anim_done
        self._on_anim_done = None
        if callback is not None:
            callback()
        return GLib.SOURCE_REMOVE

    def _cancel_tick(self) -> None:
        if self._tick_id:
            self._stage.remove_tick_callback(self._tick_id)
            self._tick_id = 0
