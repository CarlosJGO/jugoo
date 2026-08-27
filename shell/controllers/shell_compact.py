"""Centralized compact-bar mode driven by Hyprland window state."""

from __future__ import annotations

from typing import Iterable, Protocol

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ..config import HYPRLAND_MAXIMIZED_FULLSCREEN
from ..eventbus import EventBus
from ..models import ActiveWindow, HyprlandSnapshot
from ..servicios.escritorio.hyprland import (
    ACTIVE_WINDOW_CHANGED,
    FULLSCREEN_CHANGED,
    WORKSPACE_CHANGED,
    HyprlandService,
)

SHELL_COMPACT_MODE_CHANGED = "shell_compact_mode_changed"


class ShellCompactAdapter(Protocol):
    def apply_shell_compact(self, compact: bool) -> None: ...


class ShellCompactController:
    """Toggle a compact shell layout when the focused client is maximized."""

    def __init__(
        self,
        event_bus: EventBus,
        hyprland: HyprlandService,
        shell_window: Gtk.Window,
        hide_in_compact: Iterable[Gtk.Widget],
        compact_adapters: Iterable[ShellCompactAdapter] = (),
    ) -> None:
        self._event_bus = event_bus
        self._hyprland = hyprland
        self._shell_window = shell_window
        self._hide_in_compact = tuple(hide_in_compact)
        self._compact_adapters = tuple(compact_adapters)
        self._compact = False

        event_bus.subscribe(FULLSCREEN_CHANGED, self._on_hyprland_event)
        event_bus.subscribe(ACTIVE_WINDOW_CHANGED, self._on_hyprland_event)
        event_bus.subscribe(WORKSPACE_CHANGED, self._on_hyprland_event)

        GLib.idle_add(self._sync_from_hyprland)

    @property
    def compact_mode(self) -> bool:
        return self._compact

    def _sync_from_hyprland(self) -> bool:
        snapshot = self._hyprland.snapshot
        if snapshot is None:
            return False
        self._set_compact(self._should_compact(snapshot.active_window))
        return False

    def _on_hyprland_event(self, payload: object) -> None:
        active_window = _active_window_from_payload(payload)
        if active_window is None:
            return
        GLib.idle_add(self._apply_compact, self._should_compact(active_window))

    def _apply_compact(self, compact: bool) -> bool:
        self._set_compact(compact)
        return False

    def _should_compact(self, active_window: ActiveWindow) -> bool:
        if not active_window.address:
            return False
        return active_window.fullscreen == HYPRLAND_MAXIMIZED_FULLSCREEN

    def _set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact

        style = self._shell_window.get_style_context()
        if compact:
            style.add_class("compact")
        else:
            style.remove_class("compact")

        for widget in self._hide_in_compact:
            if compact:
                widget.hide()
            else:
                widget.show()

        for adapter in self._compact_adapters:
            adapter.apply_shell_compact(compact)

        self._shell_window.queue_resize()
        self._event_bus.emit(SHELL_COMPACT_MODE_CHANGED, compact)


def _active_window_from_payload(payload: object) -> ActiveWindow | None:
    if isinstance(payload, HyprlandSnapshot):
        return payload.active_window
    if isinstance(payload, ActiveWindow):
        return payload
    return None
