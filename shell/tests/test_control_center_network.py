"""Tests for control center network helpers, panel views, and navigation."""

from __future__ import annotations

import os

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gio, GLib, Gtk

from shell.eventbus import EventBus
from shell.models import NetworkConnectivitySnapshot, NetworkInterfaceSnapshot, NetworkSnapshot, WifiAccessPointSnapshot
from shell.servicios.red.network import (
    NetworkService,
    _decode_ssid,
    _wifi_connection_settings_variant,
    compose_network_snapshot,
    read_wireless_radio_state,
    wifi_scan_allowed,
)
from shell.servicios.red.network_hotspot import NM_802_11_MODE_AP, assemble_hotspot_snapshot
from shell.widgets.centro_control.views import ControlCenterView
from shell.widgets.barra.power import POWER_CONTROL_CENTER_REQUESTED
from shell.window_identity import TITLE_CONTROL_CENTER, TITLE_NETWORK_PANEL


def _iface(
    *,
    interface: str,
    device_type: str = "ethernet",
    state: str = "connected",
    device_path: str = "/org/freedesktop/NetworkManager/Devices/2",
) -> NetworkInterfaceSnapshot:
    return NetworkInterfaceSnapshot(
        interface=interface,
        device_type=device_type,
        state=state,
        connection_name="Profile",
        ip_address="192.168.1.9" if state == "connected" else "",
        link_up=state == "connected",
        speed_mbps=100 if device_type == "ethernet" and state == "connected" else None,
        mac_address="00:11:22:33:44:55",
        device_path=device_path,
    )


def test_decode_ssid_from_bytes() -> None:
    assert _decode_ssid([70, 111, 111]) == "Foo"
    assert _decode_ssid(b"Bar") == "Bar"


def test_compose_network_snapshot_includes_wifi_metadata() -> None:
    snapshot = compose_network_snapshot(
        (
            _iface(interface="enp42s0"),
            _iface(
                interface="wlan0",
                device_type="wifi",
                state="disconnected",
                device_path="/org/freedesktop/NetworkManager/Devices/3",
            ),
        ),
        connectivity=NetworkConnectivitySnapshot(level="full"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
        wifi_access_points=(
            WifiAccessPointSnapshot(
                path="/ap/1",
                ssid="Home",
                strength=80,
                secured=True,
                frequency_mhz=5180,
            ),
        ),
    )
    assert snapshot.wireless_enabled is True
    assert snapshot.wifi is not None
    assert snapshot.wifi.interface == "wlan0"
    assert len(snapshot.wifi_access_points) == 1
    assert snapshot.wifi_access_points[0].ssid == "Home"


def test_wifi_scan_allowed_requires_ready_interface() -> None:
    unavailable = compose_network_snapshot(
        (
            _iface(
                interface="wlan0",
                device_type="wifi",
                state="unavailable",
                device_path="/org/freedesktop/NetworkManager/Devices/3",
            ),
        ),
        connectivity=NetworkConnectivitySnapshot(level="unknown"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
    )
    ready = compose_network_snapshot(
        (
            _iface(
                interface="wlan0",
                device_type="wifi",
                state="disconnected",
                device_path="/org/freedesktop/NetworkManager/Devices/3",
            ),
        ),
        connectivity=NetworkConnectivitySnapshot(level="unknown"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
    )
    assert wifi_scan_allowed(unavailable) is False
    assert wifi_scan_allowed(ready) is True


def test_request_wifi_scan_skips_unavailable_interface() -> None:
    service = NetworkService(EventBus())
    service._snapshot = compose_network_snapshot(
        (
            _iface(
                interface="wlan0",
                device_type="wifi",
                state="unavailable",
                device_path="/org/freedesktop/NetworkManager/Devices/3",
            ),
        ),
        connectivity=NetworkConnectivitySnapshot(level="unknown"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
    )
    service._bus = object()
    assert service._request_wifi_scan_idle() is False


def test_read_wireless_radio_state_from_nm_proxy() -> None:
    nm_proxy = type("NM", (), {})()
    nm_proxy.get_cached_property = lambda name: {
        "WirelessEnabled": GLib.Variant("b", True),
        "WirelessHardwareEnabled": GLib.Variant("b", True),
    }[name]
    enabled, hardware = read_wireless_radio_state(nm_proxy)
    assert enabled is True
    assert hardware is True


def test_set_wireless_enabled_uses_dbus_properties_set() -> None:
    service = NetworkService(EventBus())
    calls: list[tuple] = []

    class Bus:
        def call_sync(
            self,
            _name,
            _path,
            _iface,
            method,
            params,
            reply_type,
            flags,
            timeout,
        ):
            calls.append((method, params.unpack(), reply_type, flags, timeout))
            return None

    service._bus = Bus()
    service._nm_proxy = object()
    assert service._set_wireless_enabled_idle(True) is False
    assert calls[0][0] == "Set"
    assert calls[0][1][1] == "WirelessEnabled"
    assert calls[0][1][2] is True
    assert calls[0][2] is None
    assert calls[0][3] == Gio.DBusCallFlags.NONE


def test_wifi_connection_settings_variant_includes_security() -> None:
    variant = _wifi_connection_settings_variant("Cafe", "secret")
    settings = variant.unpack()
    assert "802-11-wireless-security" in settings
    security = settings["802-11-wireless-security"]
    psk = security["psk"]
    if isinstance(psk, GLib.Variant):
        psk = psk.unpack()
    assert psk == "secret"
    ssid = settings["802-11-wireless"]["ssid"]
    if isinstance(ssid, GLib.Variant):
        ssid = ssid.unpack()
    assert _decode_ssid(ssid) == "Cafe"


def test_network_section_refresh_updates_ethernet_labels() -> None:
    from shell.widgets.centro_control.network_section import ControlCenterNetworkSection

    service = NetworkService(EventBus())
    toggles: list[bool] = []
    section = ControlCenterNetworkSection(
        service,
        on_toggle_ethernet=lambda: None,
        on_toggle_wireless=lambda enabled: toggles.append(enabled),
        on_request_wifi_scan=lambda: None,
        on_connect_wifi=lambda *_args: None,
        on_disconnect_wifi=lambda: None,
        on_toggle_hotspot=lambda *_args: None,
        on_apply_hotspot=lambda *_args: None,
    )
    snapshot = compose_network_snapshot(
        (_iface(interface="enp42s0"),),
        connectivity=NetworkConnectivitySnapshot(level="full"),
        wireless_enabled=False,
        wireless_hardware_enabled=True,
        wifi_access_points=(),
    )
    section.refresh(snapshot)
    assert "Conectado" in section._ethernet_status.get_text()
    assert "192.168.1.9" in section._ethernet_details.get_text()


def test_hotspot_section_refresh_distinguishes_sharing_from_active() -> None:
    from shell.widgets.centro_control.network_section import ControlCenterNetworkSection
    from shell.models import WifiHotspotCapabilities

    service = NetworkService(EventBus())
    section = ControlCenterNetworkSection(
        service,
        on_toggle_ethernet=lambda: None,
        on_toggle_wireless=lambda _enabled: None,
        on_request_wifi_scan=lambda: None,
        on_connect_wifi=lambda *_args: None,
        on_disconnect_wifi=lambda: None,
        on_toggle_hotspot=lambda *_args: None,
        on_apply_hotspot=lambda *_args: None,
    )
    wifi = _iface(
        interface="wlan0",
        device_type="wifi",
        state="connected",
        device_path="/org/freedesktop/NetworkManager/Devices/3",
    )
    ethernet_down = _iface(
        interface="enp42s0",
        state="disconnected",
        device_path="/org/freedesktop/NetworkManager/Devices/2",
    )
    capabilities = WifiHotspotCapabilities(
        supports_ap=True,
        supports_2_4ghz=True,
        supports_5ghz=True,
    )
    no_upstream = assemble_hotspot_snapshot(
        wifi=wifi,
        ethernet=ethernet_down,
        capabilities=capabilities,
        wifi_mode=NM_802_11_MODE_AP,
        ssid="CashlyOs-WiFi",
        band="auto",
        password_configured=True,
        ipv4_shared=True,
        clients=(),
        connection_path="/org/freedesktop/NetworkManager/Settings/9",
        pending_enable=False,
        last_error_status="",
        last_error_message="",
        wireless_hardware_enabled=True,
    )
    snapshot = compose_network_snapshot(
        (ethernet_down, wifi),
        connectivity=NetworkConnectivitySnapshot(level="none"),
        wireless_enabled=True,
        hotspot=no_upstream,
    )
    section.refresh(snapshot)
    sharing_text = section._hotspot_section._sharing.get_text()
    assert "Compartiendo Internet" not in sharing_text
    assert "Ethernet" in sharing_text

    ethernet_up = _iface(interface="enp42s0")
    sharing = assemble_hotspot_snapshot(
        wifi=wifi,
        ethernet=ethernet_up,
        capabilities=capabilities,
        wifi_mode=NM_802_11_MODE_AP,
        ssid="CashlyOs-WiFi",
        band="auto",
        password_configured=True,
        ipv4_shared=True,
        clients=(),
        connection_path="/org/freedesktop/NetworkManager/Settings/9",
        pending_enable=False,
        last_error_status="",
        last_error_message="",
        wireless_hardware_enabled=True,
    )
    section.refresh(
        compose_network_snapshot(
            (ethernet_up, wifi),
            connectivity=NetworkConnectivitySnapshot(level="full"),
            wireless_enabled=True,
            hotspot=sharing,
        ),
    )
    assert "Compartiendo Internet" in section._hotspot_section._sharing.get_text()
    assert section._hotspot_section._status.get_text() == "Compartiendo Internet"


def test_network_panel_popup_uses_network_view() -> None:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return

    from shell.widgets.centro_control.popup import ControlCenterPopup

    shell = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    service = NetworkService(EventBus())
    anchor = Gtk.Button(label="Ethernet")
    shell.add(anchor)

    popup = ControlCenterPopup(shell, service, view=ControlCenterView.NETWORK)
    assert popup.view is ControlCenterView.NETWORK
    assert popup.get_title() == TITLE_NETWORK_PANEL
    assert popup.get_name() == "shell-network-panel"

    popup.open_for(anchor)
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert popup.get_visible()

    popup.close_popup()
    shell.destroy()
    popup.destroy()


def test_full_control_center_popup_uses_full_view() -> None:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return

    from shell.widgets.centro_control.popup import ControlCenterPopup

    shell = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    service = NetworkService(EventBus())
    anchor = Gtk.Button(label="Power")
    shell.add(anchor)

    popup = ControlCenterPopup(shell, service, view=ControlCenterView.FULL)
    assert popup.view is ControlCenterView.FULL
    assert popup.get_title() == TITLE_CONTROL_CENTER
    assert popup.get_name() == "shell-control-center"

    popup.open_for(anchor)
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert popup.get_visible()

    popup.close_popup()
    shell.destroy()
    popup.destroy()


def _make_controller_stub(*, network_visible: bool = False, full_visible: bool = False):
    from shell.controllers.control_center import ControlCenterController

    opened = {"network": False, "full": False}

    class Widget:
        def get_anchor_button(self):
            return object()

    controller = ControlCenterController.__new__(ControlCenterController)
    controller._outside_click = type("Dismiss", (), {
        "install": lambda *args, **kwargs: None,
        "uninstall": lambda *args, **kwargs: None,
    })()
    controller._ethernet_widget = Widget()
    controller._power_widget = type("Power", (), {
        "close_menu": lambda *_args, **_kwargs: None,
        "get_anchor_button": Widget().get_anchor_button,
    })()
    controller._shell_window = object()
    controller._event_bus = EventBus()
    controller._active_panel = None
    controller._active_anchor = None
    controller._network_panel = type("PopupHandle", (), {
        "is_visible": lambda self: network_visible,
        "maybe": None,
        "get": lambda self: type("Popup", (), {
            "open_for": lambda *_args: opened.__setitem__("network", True),
            "close_popup": lambda: None,
        })(),
    })()
    controller._full_center = type("PopupHandle", (), {
        "is_visible": lambda self: full_visible,
        "maybe": None,
        "get": lambda self: type("Popup", (), {
            "open_for": lambda *_args: opened.__setitem__("full", True),
            "close_popup": lambda: None,
        })(),
    })()
    for name in (
        "close_popup",
        "toggle_network_panel",
        "toggle_full_control_center",
        "_open_panel",
        "_handle_network_panel_requested",
        "_handle_full_control_center_requested",
    ):
        setattr(controller, name, getattr(ControlCenterController, name).__get__(controller))
    return controller, opened


def test_ethernet_click_opens_network_panel_only() -> None:
    controller, opened = _make_controller_stub()
    controller._handle_network_panel_requested()
    assert opened["network"] is True
    assert opened["full"] is False


def test_power_right_click_event_opens_full_control_center() -> None:
    controller, opened = _make_controller_stub()
    controller._handle_full_control_center_requested()
    assert opened["full"] is True
    assert opened["network"] is False


def test_power_event_constant_is_registered() -> None:
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(POWER_CONTROL_CENTER_REQUESTED, seen.append)
    bus.emit(POWER_CONTROL_CENTER_REQUESTED, object())
    assert seen


if __name__ == "__main__":
    test_decode_ssid_from_bytes()
    test_compose_network_snapshot_includes_wifi_metadata()
    test_wifi_scan_allowed_requires_ready_interface()
    test_request_wifi_scan_skips_unavailable_interface()
    test_read_wireless_radio_state_from_nm_proxy()
    test_set_wireless_enabled_uses_dbus_properties_set()
    test_wifi_connection_settings_variant_includes_security()
    test_network_section_refresh_updates_ethernet_labels()
    test_hotspot_section_refresh_distinguishes_sharing_from_active()
    test_network_panel_popup_uses_network_view()
    test_full_control_center_popup_uses_full_view()
    test_ethernet_click_opens_network_panel_only()
    test_power_right_click_event_opens_full_control_center()
    test_power_event_constant_is_registered()
    print("control center network tests OK")
