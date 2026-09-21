"""Compact battery indicator for the top bar."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ... import config as shell_config
from ...config import BATTERY_ICON_SIZE
from ...eventbus import EventBus
from ...models import BatterySnapshot
from ...servicios.energia.battery import (
    BATTERY_CHANGED,
    BatteryService,
    battery_should_show,
    build_battery_tooltip,
)
from ...settings.manager import SETTINGS_CHANGED
from ...ui import ShellModule


class BatteryWidget(ShellModule):
    """Shows icon + percentage when a battery is present (auto-hides on desktops)."""

    def __init__(
        self,
        event_bus: EventBus,
        battery_service: BatteryService,
    ) -> None:
        super().__init__("battery-widget", spacing=4)

        self._event_bus = event_bus
        self._service = battery_service
        self._snapshot = battery_service.snapshot
        self._visibility = str(getattr(shell_config, "BATTERY_VISIBILITY", "auto"))

        self._icon = Gtk.Image.new_from_icon_name(
            "battery-missing-symbolic",
            Gtk.IconSize.MENU,
        )
        self._icon.get_style_context().add_class("battery-icon")
        self._icon.set_pixel_size(BATTERY_ICON_SIZE)

        self._label = Gtk.Label(label="")
        self._label.get_style_context().add_class("battery-percent")
        self._label.set_xalign(0)

        self.pack_start(self._icon, False, False, 0)
        self.pack_start(self._label, False, False, 0)
        self.set_tooltip_text("")

        self._event_bus.subscribe(BATTERY_CHANGED, self._on_battery_changed)
        self._event_bus.subscribe(SETTINGS_CHANGED, self._on_settings_changed)
        self.connect("destroy", self._on_destroy)
        self.hide()
        GLib.idle_add(self._apply_snapshot, self._snapshot)

    def set_visibility(self, visibility: str) -> None:
        self._visibility = str(visibility or "auto")
        self._apply_snapshot(self._snapshot)

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(BATTERY_CHANGED, self._on_battery_changed)
        self._event_bus.unsubscribe(SETTINGS_CHANGED, self._on_settings_changed)

    def _on_battery_changed(self, snapshot: object) -> None:
        if isinstance(snapshot, BatterySnapshot):
            GLib.idle_add(self._apply_snapshot, snapshot)

    def _on_settings_changed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("key") != "widgets.battery_visibility":
            return
        self.set_visibility(str(payload.get("value") or "auto"))

    def _apply_snapshot(self, snapshot: BatterySnapshot) -> bool:
        self._snapshot = snapshot
        if not battery_should_show(snapshot, self._visibility):
            self.hide()
            return False

        self._icon.set_from_icon_name(snapshot.icon_name, Gtk.IconSize.MENU)
        self._icon.set_pixel_size(BATTERY_ICON_SIZE)
        self._label.set_text(snapshot.percent_label)
        self.set_tooltip_text(build_battery_tooltip(snapshot))

        style = self.get_style_context()
        for name in (
            "battery-charging",
            "battery-full",
            "battery-low",
            "battery-critical",
            "battery-discharging",
        ):
            style.remove_class(name)
        if snapshot.charging:
            style.add_class("battery-charging")
        elif snapshot.full:
            style.add_class("battery-full")
        elif snapshot.critical:
            style.add_class("battery-critical")
        elif snapshot.low:
            style.add_class("battery-low")
        else:
            style.add_class("battery-discharging")

        self.show_all()
        return False
