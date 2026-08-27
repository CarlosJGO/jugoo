"""Compact ethernet indicator driven by NetworkService snapshots."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import NETWORK_COMPACT_ICON_SIZE, NETWORK_ICON_SIZE
from ...eventbus import EventBus
from ...models import NetworkSnapshot
from ...servicios.red.network import (
    NETWORK_CHANGED,
    NETWORK_ETHERNET_CLICKED,
    NetworkService,
    build_ethernet_tooltip,
    ethernet_icon_name,
    ethernet_visual_state,
)
from ...ui import ShellModule


class EthernetWidget(ShellModule):
    """Bar indicator for wired network; left click opens the network panel."""

    def __init__(
        self,
        event_bus: EventBus,
        network_service: NetworkService,
    ) -> None:
        super().__init__("ethernet-widget", spacing=0)

        self._event_bus = event_bus
        self._service = network_service
        self._compact = False
        self._snapshot = network_service.snapshot
        self._pulse_source_id = 0
        self._pulse_opacity = 1.0
        self._pulse_dir = -1
        self._visual_state = ""

        self._button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._button.get_style_context().add_class("ethernet-button")
        self._button.connect("button-press-event", self._on_button_press)

        self._icon = Gtk.Image.new_from_icon_name(
            "network-wired-disconnected-symbolic",
            Gtk.IconSize.MENU,
        )
        self._icon.get_style_context().add_class("ethernet-icon")
        self._icon.set_pixel_size(NETWORK_ICON_SIZE)
        self._button.add(self._icon)
        self.pack_start(self._button, False, False, 0)

        self._event_bus.subscribe(NETWORK_CHANGED, self._on_network_changed)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._apply_snapshot, self._snapshot)

    def get_anchor_button(self) -> Gtk.Widget:
        return self._button

    def apply_shell_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._icon.set_pixel_size(
            NETWORK_COMPACT_ICON_SIZE if compact else NETWORK_ICON_SIZE
        )

    def _on_destroy(self, *_args) -> None:
        self._stop_pulse()
        self._event_bus.unsubscribe(NETWORK_CHANGED, self._on_network_changed)

    def _on_network_changed(self, snapshot: NetworkSnapshot) -> None:
        GLib.idle_add(self._apply_snapshot, snapshot)

    def _apply_snapshot(self, snapshot: NetworkSnapshot) -> bool:
        self._snapshot = snapshot
        icon_name = ethernet_icon_name(snapshot)
        if icon_name is None:
            self._stop_pulse()
            self.hide()
            return False

        self._icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)
        self._icon.set_pixel_size(
            NETWORK_COMPACT_ICON_SIZE if self._compact else NETWORK_ICON_SIZE
        )
        self._button.set_tooltip_text(build_ethernet_tooltip(snapshot))
        self._apply_visual_state(ethernet_visual_state(snapshot))
        self.show_all()
        return False

    def _apply_visual_state(self, state: str | None) -> None:
        next_state = state or ""
        style = self._button.get_style_context()
        if next_state != self._visual_state:
            if self._visual_state:
                style.remove_class(f"ethernet-state-{self._visual_state}")
            if next_state:
                style.add_class(f"ethernet-state-{next_state}")
            self._visual_state = next_state
        if next_state == "connecting":
            self._start_pulse()
        else:
            self._stop_pulse()

    def _start_pulse(self) -> None:
        if self._pulse_source_id:
            return
        self._pulse_opacity = 1.0
        self._pulse_dir = -1
        self._pulse_source_id = GLib.timeout_add(70, self._pulse_tick)

    def _stop_pulse(self) -> None:
        if self._pulse_source_id:
            GLib.source_remove(self._pulse_source_id)
            self._pulse_source_id = 0
        self._pulse_opacity = 1.0
        self._icon.set_opacity(1.0)

    def _pulse_tick(self) -> bool:
        self._pulse_opacity += 0.08 * self._pulse_dir
        if self._pulse_opacity <= 0.42:
            self._pulse_opacity = 0.42
            self._pulse_dir = 1
        elif self._pulse_opacity >= 1.0:
            self._pulse_opacity = 1.0
            self._pulse_dir = -1
        self._icon.set_opacity(self._pulse_opacity)
        return True

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._event_bus.emit(NETWORK_ETHERNET_CLICKED, self._snapshot.ethernet)
            return True
        if event.button == 3:
            self._service.toggle_ethernet()
            return True
        return False
