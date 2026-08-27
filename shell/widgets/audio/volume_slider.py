"""Reusable volume slider widget shared between workspace popup and panel."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GObject, Gtk


class VolumeSlider(Gtk.Box):
    """Encapsulates a horizontal Gtk.Scale slider for controlling volume."""

    __gsignals__ = {
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(
        self,
        initial_volume: float = 1.0,
        on_change: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.get_style_context().add_class("volume-slider")

        self._updating_programmatically = False
        self._is_dragging = False
        self._is_alive = True

        self._scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01
        )
        self._scale.set_draw_value(False)
        self._scale.set_value(max(0.0, min(1.0, initial_volume)))
        self._scale.set_hexpand(True)
        self._scale.set_size_request(90, -1)
        self._scale.set_can_focus(True)
        self._scale.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
        )
        self._scale.get_style_context().add_class("audio-scale")

        self.pack_start(self._scale, True, True, 0)

        if on_change is not None:
            self.connect("volume-changed", lambda _w, vol: on_change(vol))

        self._scale.connect("button-press-event", self._on_button_press)
        self._scale.connect("button-release-event", self._on_button_release)
        self._scale.connect("change-value", self._on_change_value)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)

    @property
    def is_dragging(self) -> bool:
        return self._is_dragging

    def set_volume(self, volume: float) -> None:
        """Update scale position without interrupting an active drag."""
        if not self._is_alive or self._scale.props.parent is None:
            return
        if self.is_dragging:
            return
        clamped = max(0.0, min(1.0, volume))
        if abs(self._scale.get_value() - clamped) < 0.005:
            return
        self._updating_programmatically = True
        self._scale.set_value(clamped)
        self._updating_programmatically = False

    def get_volume(self) -> float:
        return self._scale.get_value()

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._is_dragging = True
        return False

    def _on_button_release(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._is_dragging = False
        return False

    def _on_unmap(self, *_args) -> None:
        self._is_dragging = False

    def _on_destroy(self, *_args) -> None:
        self._is_alive = False

    def _on_change_value(
        self,
        _scale: Gtk.Scale,
        _scroll: Gtk.ScrollType,
        value: float,
    ) -> bool:
        if self._updating_programmatically:
            return False
        event = Gtk.get_current_event()
        if event is not None and (event.state & Gdk.ModifierType.BUTTON1_MASK):
            self._is_dragging = True
        self.emit("volume-changed", max(0.0, min(1.0, value)))
        return False
