"""Control center popup shell with single-section and full layouts."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import (
    CONTROL_CENTER_POPUP_MAX_HEIGHT,
    CONTROL_CENTER_POPUP_OFFSET,
    CONTROL_CENTER_POPUP_WIDTH,
)
from ...popup_handle import pointer_inside_widget, present_popup, hide_popup
from ...popup_spawn import publish_popup_spawn
from ...servicios.bluetooth.bluetooth import BluetoothService
from ...servicios.red.network import NetworkService, wifi_scan_allowed
from ...ui.starfield import install_starfield, resolve_event_bus
from ...window_identity import (
    TITLE_BLUETOOTH_PANEL,
    TITLE_CONTROL_CENTER,
    TITLE_NETWORK_PANEL,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from .bluetooth_section import ControlCenterBluetoothSection
from .network_section import ControlCenterNetworkSection
from .placeholder_section import ControlCenterPlaceholderSection
from .views import ControlCenterView

_VIEW_WINDOW_NAMES = {
    ControlCenterView.FULL: "shell-control-center",
    ControlCenterView.NETWORK: "shell-network-panel",
    ControlCenterView.BLUETOOTH: "shell-bluetooth-panel",
    ControlCenterView.AUDIO: "shell-audio-panel",
    ControlCenterView.MEDIA: "shell-media-panel",
}

_VIEW_TITLES = {
    ControlCenterView.FULL: TITLE_CONTROL_CENTER,
    ControlCenterView.NETWORK: TITLE_NETWORK_PANEL,
    ControlCenterView.BLUETOOTH: TITLE_BLUETOOTH_PANEL,
    ControlCenterView.AUDIO: TITLE_CONTROL_CENTER,
    ControlCenterView.MEDIA: TITLE_CONTROL_CENTER,
}

_VIEW_HEADINGS = {
    ControlCenterView.FULL: "Centro de control",
    ControlCenterView.NETWORK: "Red",
    ControlCenterView.BLUETOOTH: "Bluetooth",
    ControlCenterView.AUDIO: "Audio",
    ControlCenterView.MEDIA: "Media",
}


class ControlCenterPopup(Gtk.Window):
    """Anchored popup that can show one section or the full control center."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        network_service: NetworkService,
        bluetooth_service: BluetoothService,
        *,
        view: ControlCenterView = ControlCenterView.FULL,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._service = network_service
        self._bluetooth_service = bluetooth_service
        self._view = view
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._last_height = 0
        self._network_section: ControlCenterNetworkSection | None = None
        self._bluetooth_section: ControlCenterBluetoothSection | None = None

        self.set_name(_VIEW_WINDOW_NAMES[view])
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=_VIEW_TITLES[view])
        configure_interactive_popup(self)
        self.set_default_size(CONTROL_CENTER_POPUP_WIDTH, -1)
        self.connect("size-allocate", self._on_size_allocate)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("control-center-popup-content")
        install_starfield(
            self,
            outer,
            resolve_event_bus(shell_window),
        )

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.get_style_context().add_class("control-center-popup-header")
        title = Gtk.Label(label=_VIEW_HEADINGS[view], xalign=0)
        title.get_style_context().add_class("control-center-popup-title")
        header.pack_start(title, False, False, 0)
        outer.pack_start(header, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(CONTROL_CENTER_POPUP_MAX_HEIGHT)
        scrolled.get_style_context().add_class("control-center-popup-scroll")
        outer.pack_start(scrolled, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.get_style_context().add_class("control-center-popup-body")
        scrolled.add(body)

        if view in (ControlCenterView.FULL, ControlCenterView.NETWORK):
            self._network_section = ControlCenterNetworkSection(
                network_service,
                on_toggle_ethernet=network_service.toggle_ethernet,
                on_toggle_wireless=network_service.set_wireless_enabled,
                on_request_wifi_scan=network_service.request_wifi_scan,
                on_connect_wifi=lambda path, password: network_service.connect_wifi(
                    path,
                    password,
                ),
                on_disconnect_wifi=network_service.disconnect_wifi,
                on_toggle_hotspot=lambda enabled, ssid, password, band: (
                    network_service.set_hotspot_enabled(
                        enabled,
                        ssid=ssid,
                        password=password,
                        band=band,
                    )
                ),
                on_apply_hotspot=network_service.apply_hotspot_config,
            )
            body.pack_start(self._network_section, False, False, 0)

        if view in (ControlCenterView.FULL, ControlCenterView.BLUETOOTH):
            self._bluetooth_section = ControlCenterBluetoothSection(
                bluetooth_service,
                on_toggle_powered=bluetooth_service.set_powered,
                on_start_discovery=bluetooth_service.start_discovery,
                on_stop_discovery=bluetooth_service.stop_discovery,
                on_set_receiving=bluetooth_service.set_receiving,
                on_connect=bluetooth_service.connect_device,
                on_disconnect=bluetooth_service.disconnect_device,
                on_pair=bluetooth_service.pair_device,
                on_remove=bluetooth_service.remove_device,
                on_send_file=bluetooth_service.send_file,
                on_cancel_transfer=bluetooth_service.cancel_transfer,
                on_accept_incoming=bluetooth_service.accept_incoming_file,
                on_reject_incoming=bluetooth_service.reject_incoming_file,
                on_accept_pairing=lambda **kwargs: bluetooth_service.accept_pairing(**kwargs),
                on_reject_pairing=bluetooth_service.reject_pairing,
            )
            body.pack_start(self._bluetooth_section, False, False, 0)

        if view == ControlCenterView.FULL:
            body.pack_start(
                ControlCenterPlaceholderSection(
                    "Audio",
                    module_class="control-center-audio-section",
                ),
                False,
                False,
                0,
            )
            body.pack_start(
                ControlCenterPlaceholderSection(
                    "Media",
                    module_class="control-center-media-section",
                ),
                False,
                False,
                0,
            )
            body.pack_start(
                ControlCenterPlaceholderSection(
                    "Notificaciones",
                    module_class="control-center-notifications-section",
                ),
                False,
                False,
                0,
            )

    @property
    def view(self) -> ControlCenterView:
        return self._view

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._last_height = 0
        self.refresh()
        if (
            self._network_section is not None
            and wifi_scan_allowed(self._service.snapshot)
        ):
            self._service.request_wifi_scan()
        publish_popup_spawn(
            self,
            anchor_button,
            title=_VIEW_TITLES[self._view],
            offset=CONTROL_CENTER_POPUP_OFFSET,
        )
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor_button = None
        self._fixed_popup_top = None
        self._last_height = 0
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self) -> None:
        if self._network_section is not None:
            self._network_section.refresh(self._service.snapshot)
        if self._bluetooth_section is not None:
            self._bluetooth_section.refresh(self._bluetooth_service.snapshot)
        # Height changes are caught by size-allocate; keep the top edge locked.
        if self.get_visible():
            self.queue_resize()

    def _on_size_allocate(self, _widget: Gtk.Widget, allocation: Gtk.Allocation) -> None:
        height = int(allocation.height)
        if height <= 1 or height == self._last_height:
            return
        self._last_height = height
        self._reposition()

    def _reposition(self) -> None:
        if self.get_visible() and self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._anchor_button is None:
            return False
        top = position_popup_below_anchor(
            self,
            self._anchor_button,
            title=_VIEW_TITLES[self._view],
            offset=CONTROL_CENTER_POPUP_OFFSET,
            fixed_top=self._fixed_popup_top,
        )
        if self._fixed_popup_top is None and top is not None:
            self._fixed_popup_top = top
        return False
