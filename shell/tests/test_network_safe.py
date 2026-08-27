"""Safe tests for NetworkService snapshot helpers and mocked D-Bus actions."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from shell.eventbus import EventBus
from shell.models import NetworkConnectivitySnapshot, NetworkInterfaceSnapshot, NetworkSnapshot
from shell.servicios.red.network import (
    NM_CONNECTIVITY_FULL,
    NM_CONNECTIVITY_LIMITED,
    NM_CONNECTIVITY_NONE,
    NM_DEVICE_STATE_ACTIVATED,
    NM_DEVICE_STATE_DISCONNECTED,
    NM_DEVICE_STATE_UNAVAILABLE,
    NetworkService,
    build_ethernet_tooltip,
    compose_network_snapshot,
    connectivity_label,
    device_state_label,
    ethernet_icon_name,
    ethernet_visual_state,
    format_link_speed,
    read_manager_connectivity,
    select_ethernet_interface,
    select_primary_interface,
    should_schedule_connectivity_followup,
)


def _iface(
    *,
    interface: str = "enp42s0",
    device_type: str = "ethernet",
    state: str = "connected",
    ip_address: str = "192.168.1.9",
    speed_mbps: int | None = 1000,
    device_path: str = "/org/freedesktop/NetworkManager/Devices/2",
) -> NetworkInterfaceSnapshot:
    return NetworkInterfaceSnapshot(
        interface=interface,
        device_type=device_type,
        state=state,
        connection_name="Conexión cableada 1",
        ip_address=ip_address,
        link_up=state == "connected",
        speed_mbps=speed_mbps,
        mac_address="00:11:22:33:44:55",
        device_path=device_path,
    )


def _snapshot(
    *,
    ethernet: NetworkInterfaceSnapshot | None = None,
    connectivity: str = "full",
) -> NetworkSnapshot:
    interfaces = (ethernet,) if ethernet is not None else ()
    return compose_network_snapshot(
        interfaces,
        connectivity=NetworkConnectivitySnapshot.from_nm_label(connectivity),
    )


def test_icon_sequence_reconnect_full() -> None:
    connected_full = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    disconnected = _snapshot(
        ethernet=_iface(state="disconnected", ip_address="", speed_mbps=None),
        connectivity="none",
    )
    connected_stale = _snapshot(ethernet=_iface(state="connected"), connectivity="limited")
    connected_full_again = _snapshot(ethernet=_iface(state="connected"), connectivity="full")

    assert ethernet_icon_name(connected_full) == "network-wired-symbolic"
    assert ethernet_icon_name(disconnected) == "network-wired-disconnected-symbolic"
    assert ethernet_icon_name(connected_stale) == "network-wired-no-route-symbolic"
    assert ethernet_icon_name(connected_full_again) == "network-wired-symbolic"
    assert build_ethernet_tooltip(connected_full_again).splitlines()[1] == "Conectado · Internet"


def test_icon_sequence_internet_lost() -> None:
    connected_full = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    connected_no_internet = _snapshot(ethernet=_iface(state="connected"), connectivity="none")

    assert ethernet_icon_name(connected_full) == "network-wired-symbolic"
    assert ethernet_icon_name(connected_no_internet) == "network-wired-no-route-symbolic"
    assert ethernet_visual_state(connected_full) == "connected"
    assert ethernet_visual_state(connected_no_internet) == "no-internet"
    assert "Sin Internet" in build_ethernet_tooltip(connected_no_internet)


def test_should_schedule_connectivity_followup_after_reconnect() -> None:
    previous = _snapshot(
        ethernet=_iface(state="disconnected", ip_address="", speed_mbps=None),
        connectivity="none",
    )
    current = _snapshot(ethernet=_iface(state="connected"), connectivity="limited")
    assert should_schedule_connectivity_followup(previous, current) is True


def test_should_schedule_connectivity_followup_when_internet_lost() -> None:
    previous = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    current = _snapshot(ethernet=_iface(state="connected"), connectivity="limited")
    assert should_schedule_connectivity_followup(previous, current) is True


def test_should_not_schedule_connectivity_followup_when_stable() -> None:
    previous = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    current = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    assert should_schedule_connectivity_followup(previous, current) is False


def test_read_manager_connectivity_force_check() -> None:
    nm_proxy = type("NM", (), {})()

    def call_sync(method, _params, *_args, **_kwargs):
        assert method == "CheckConnectivity"
        return GLib.Variant("(u)", (NM_CONNECTIVITY_FULL,))

    nm_proxy.call_sync = call_sync
    nm_proxy.get_cached_property = lambda _name: GLib.Variant("u", NM_CONNECTIVITY_NONE)

    snapshot = read_manager_connectivity(nm_proxy, force_check=True)
    assert snapshot.level == "full"
    assert snapshot.has_internet is True


def test_toggle_connect_schedules_connectivity_followup() -> None:
    service = NetworkService(EventBus())
    service._nm_proxy = object()
    service._snapshot = _snapshot(
        ethernet=_iface(state="disconnected", ip_address="", speed_mbps=None),
        connectivity="none",
    )
    scheduled: list[str] = []
    service._connect_device = lambda _path: True
    service._disconnect_device = lambda _path: True
    service._schedule_connectivity_followup = lambda *, trigger: scheduled.append(trigger)
    service._cancel_connectivity_followup = lambda: None
    service._schedule_refresh = lambda *_args, **_kwargs: None

    service._toggle_ethernet_idle()
    assert scheduled == ["toggle-connect"]


def test_toggle_disconnect_cancels_connectivity_followup() -> None:
    service = NetworkService(EventBus())
    service._nm_proxy = object()
    service._snapshot = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    cancelled = {"value": False}
    refreshed = {"value": False}
    service._connect_device = lambda _path: True
    service._disconnect_device = lambda _path: True
    service._schedule_connectivity_followup = lambda *, trigger: None
    service._cancel_connectivity_followup = lambda: cancelled.__setitem__("value", True)
    service._schedule_refresh = lambda *_args, **_kwargs: refreshed.__setitem__("value", True)

    service._toggle_ethernet_idle()
    assert cancelled["value"] is True
    assert refreshed["value"] is True


def test_connectivity_followup_updates_snapshot_with_force_check() -> None:
    service = NetworkService(EventBus())
    service._nm_proxy = object()
    service._bus = object()
    service._device_watchers = {}
    calls = {"count": 0, "force": False}

    def fake_refresh(*, emit: bool, force_connectivity_check: bool = False) -> None:
        calls["count"] += 1
        if force_connectivity_check:
            calls["force"] = True
        service._snapshot = _snapshot(
            ethernet=_iface(state="connected"),
            connectivity="full" if calls["count"] >= 2 else "limited",
        )

    service._refresh_snapshot = fake_refresh
    service._schedule_connectivity_followup(trigger="test")
    assert calls["count"] == 1
    assert calls["force"] is True
    assert service._connectivity_followup_delays

    assert service._connectivity_followup_tick() is False
    assert service._snapshot.connectivity.has_internet is True


def test_device_state_label_maps_network_manager_states() -> None:
    assert device_state_label(NM_DEVICE_STATE_ACTIVATED) == "connected"
    assert device_state_label(NM_DEVICE_STATE_DISCONNECTED) == "disconnected"
    assert device_state_label(NM_DEVICE_STATE_UNAVAILABLE) == "unavailable"


def test_format_link_speed() -> None:
    assert format_link_speed(None) == ""
    assert format_link_speed(0) == ""
    assert format_link_speed(100) == "100 Mbps"
    assert format_link_speed(1000) == "1 Gbps"
    assert format_link_speed(2500) == "2.5 Gbps"


def test_ethernet_icon_connected_with_full_internet() -> None:
    snapshot = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    assert ethernet_icon_name(snapshot) == "network-wired-symbolic"


def test_ethernet_icon_connected_without_internet() -> None:
    for level in ("limited", "none", "portal"):
        snapshot = _snapshot(ethernet=_iface(state="connected"), connectivity=level)
        assert ethernet_icon_name(snapshot) == "network-wired-no-route-symbolic"


def test_ethernet_icon_disconnected_and_transition() -> None:
    disconnected = _snapshot(
        ethernet=_iface(state="disconnected", ip_address=""),
        connectivity="none",
    )
    connecting = _snapshot(
        ethernet=_iface(state="connecting", ip_address=""),
        connectivity="unknown",
    )
    assert ethernet_icon_name(disconnected) == "network-wired-disconnected-symbolic"
    assert ethernet_icon_name(connecting) == "network-wired-acquiring-symbolic"
    assert ethernet_visual_state(disconnected) == "disconnected"
    assert ethernet_visual_state(connecting) == "connecting"


def test_ethernet_icon_hidden_without_ethernet() -> None:
    snapshot = compose_network_snapshot(
        (),
        connectivity=NetworkConnectivitySnapshot(level="unknown"),
    )
    assert ethernet_icon_name(snapshot) is None


def test_compose_network_snapshot_prefers_connected_ethernet() -> None:
    interfaces = (
        _iface(interface="enp42s0", state="connected"),
        NetworkInterfaceSnapshot(
            interface="wlan0",
            device_type="wifi",
            state="disconnected",
            connection_name="",
            ip_address="",
            link_up=False,
            speed_mbps=None,
            mac_address="aa:bb:cc:dd:ee:ff",
        ),
    )
    snapshot = compose_network_snapshot(
        interfaces,
        connectivity=NetworkConnectivitySnapshot.from_nm_label(
            connectivity_label(NM_CONNECTIVITY_FULL),
        ),
    )
    assert snapshot.ethernet is not None
    assert snapshot.ethernet.interface == "enp42s0"
    assert snapshot.wifi is not None
    assert snapshot.wifi.interface == "wlan0"
    assert snapshot.primary is not None
    assert snapshot.primary.device_type == "ethernet"
    assert snapshot.connectivity.level == "full"
    assert snapshot.connectivity.has_internet is True


def test_select_ethernet_ignores_wifi() -> None:
    interfaces = (
        _iface(state="disconnected", ip_address=""),
        NetworkInterfaceSnapshot(
            interface="wlan0",
            device_type="wifi",
            state="connected",
            connection_name="WiFi",
            ip_address="10.0.0.2",
            link_up=True,
            speed_mbps=None,
            mac_address="aa:bb:cc:dd:ee:ff",
        ),
    )
    assert select_ethernet_interface(interfaces).interface == "enp42s0"
    assert select_primary_interface(interfaces).interface == "wlan0"


def test_build_ethernet_tooltip_connected_with_internet() -> None:
    tooltip = build_ethernet_tooltip(
        _snapshot(ethernet=_iface(interface="enp42s0"), connectivity="full"),
    )
    assert tooltip.splitlines()[:2] == ["Ethernet", "Conectado · Internet"]
    assert "192.168.1.9" in tooltip
    assert "enp42s0" in tooltip
    assert "1 Gbps" in tooltip


def test_build_ethernet_tooltip_connected_without_internet() -> None:
    tooltip = build_ethernet_tooltip(
        _snapshot(ethernet=_iface(interface="enp42s0", speed_mbps=100), connectivity="limited"),
    )
    assert "Conectado · Sin Internet" in tooltip
    assert "100 Mbps" in tooltip


def test_build_ethernet_tooltip_disconnected() -> None:
    tooltip = build_ethernet_tooltip(
        _snapshot(
            ethernet=_iface(state="disconnected", ip_address="", speed_mbps=None),
            connectivity="none",
        ),
    )
    assert tooltip.splitlines() == ["Ethernet", "Desconectado", "Interfaz: enp42s0"]


def test_connectivity_snapshot_labels() -> None:
    assert NetworkConnectivitySnapshot(level="full").summary_label == "Internet"
    assert NetworkConnectivitySnapshot(level="limited").summary_label == "Sin Internet"
    assert NetworkConnectivitySnapshot(level="portal").summary_label == "Portal cautivo"
    assert NetworkConnectivitySnapshot(level="none").summary_label == "Sin Internet"


def test_toggle_ethernet_requests_connect_when_disconnected() -> None:
    service = NetworkService(EventBus())
    service._nm_proxy = object()
    service._snapshot = _snapshot(
        ethernet=_iface(state="disconnected", ip_address=""),
        connectivity="none",
    )
    calls: list[str] = []
    service._connect_device = lambda path: calls.append(f"connect:{path}") or True
    service._disconnect_device = lambda path: calls.append(f"disconnect:{path}") or True
    service._schedule_connectivity_followup = lambda *, trigger: calls.append(f"followup:{trigger}")
    service._cancel_connectivity_followup = lambda: None
    service._schedule_refresh = lambda *_args, **_kwargs: None

    assert service._toggle_ethernet_idle() is False
    assert calls == [
        "connect:/org/freedesktop/NetworkManager/Devices/2",
        "followup:toggle-connect",
    ]


def test_toggle_ethernet_requests_disconnect_when_connected() -> None:
    service = NetworkService(EventBus())
    service._nm_proxy = object()
    service._snapshot = _snapshot(ethernet=_iface(state="connected"), connectivity="full")
    calls: list[str] = []
    service._connect_device = lambda path: calls.append(f"connect:{path}") or True
    service._disconnect_device = lambda path: calls.append(f"disconnect:{path}") or True
    service._schedule_connectivity_followup = lambda *, trigger: calls.append(f"followup:{trigger}")
    service._cancel_connectivity_followup = lambda: calls.append("cancel-followup")
    service._schedule_refresh = lambda *_args, **_kwargs: calls.append("refresh")

    assert service._toggle_ethernet_idle() is False
    assert calls == [
        "disconnect:/org/freedesktop/NetworkManager/Devices/2",
        "cancel-followup",
        "refresh",
    ]


def test_connect_device_uses_activate_connection_dbus() -> None:
    service = NetworkService(EventBus())
    nm_proxy = type("NM", (), {})()
    nm_proxy.calls: list[tuple[str, object]] = []

    def call_sync(method, parameters, *_args, **_kwargs):
        nm_proxy.calls.append((method, parameters.unpack()))
        return None

    nm_proxy.call_sync = call_sync
    service._nm_proxy = nm_proxy
    service._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    real_new_sync = Gio.DBusProxy.new_sync

    def fake_new_sync(_bus, _flags, _info, _name, path, iface, _cancel):
        if path == "/org/freedesktop/NetworkManager/Devices/2":
            return type(
                "DeviceProxy",
                (),
                {
                    "get_cached_property": staticmethod(
                        lambda _name: GLib.Variant("ao", ["/org/freedesktop/NetworkManager/Settings/2"]),
                    ),
                },
            )()
        return real_new_sync(_bus, _flags, _info, _name, path, iface, _cancel)

    Gio.DBusProxy.new_sync = fake_new_sync
    try:
        assert service._connect_device("/org/freedesktop/NetworkManager/Devices/2") is True
    finally:
        Gio.DBusProxy.new_sync = real_new_sync

    assert nm_proxy.calls == [
        (
            "ActivateConnection",
            (
                "/org/freedesktop/NetworkManager/Settings/2",
                "/org/freedesktop/NetworkManager/Devices/2",
                "/",
            ),
        ),
    ]


def test_disconnect_device_uses_device_disconnect_dbus() -> None:
    service = NetworkService(EventBus())
    service._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    calls: list[str] = []

    class DeviceProxy:
        def call_sync(self, method, _params, *_args, **_kwargs):
            calls.append(method)
            return None

    real_new_sync = Gio.DBusProxy.new_sync

    def fake_new_sync(_bus, _flags, _info, _name, path, _iface, _cancel):
        if path == "/org/freedesktop/NetworkManager/Devices/2":
            return DeviceProxy()
        return real_new_sync(_bus, _flags, _info, _name, path, _iface, _cancel)

    Gio.DBusProxy.new_sync = fake_new_sync
    try:
        assert service._disconnect_device("/org/freedesktop/NetworkManager/Devices/2") is True
    finally:
        Gio.DBusProxy.new_sync = real_new_sync

    assert calls == ["Disconnect"]


def test_network_service_emits_on_refresh() -> None:
    event_bus = EventBus()
    seen: list[object] = []
    event_bus.subscribe("network_changed", seen.append)
    service = NetworkService(event_bus)
    service.start()
    try:
        assert service.available, "NetworkManager should be available on this system"
        assert service.snapshot.ethernet is not None
        assert service.snapshot.ethernet.device_type == "ethernet"
        assert seen, "initial refresh should emit network_changed"
    finally:
        service.close()


def test_network_service_reads_live_ethernet_fields() -> None:
    service = NetworkService(EventBus())
    service.start()
    try:
        if not service.available:
            return
        ethernet = service.snapshot.ethernet
        assert ethernet is not None
        assert ethernet.interface
        assert ethernet.state in {
            "connected",
            "disconnected",
            "unavailable",
            "connecting",
            "disconnecting",
            "failed",
            "unmanaged",
            "unknown",
        }
        if ethernet.state == "connected":
            assert ethernet.ip_address
        assert service.snapshot.connectivity.level in {
            "full",
            "limited",
            "portal",
            "none",
            "unknown",
        }
    finally:
        service.close()


if __name__ == "__main__":
    test_icon_sequence_reconnect_full()
    test_icon_sequence_internet_lost()
    test_should_schedule_connectivity_followup_after_reconnect()
    test_should_schedule_connectivity_followup_when_internet_lost()
    test_should_not_schedule_connectivity_followup_when_stable()
    test_read_manager_connectivity_force_check()
    test_toggle_connect_schedules_connectivity_followup()
    test_toggle_disconnect_cancels_connectivity_followup()
    test_connectivity_followup_updates_snapshot_with_force_check()
    test_device_state_label_maps_network_manager_states()
    test_format_link_speed()
    test_ethernet_icon_connected_with_full_internet()
    test_ethernet_icon_connected_without_internet()
    test_ethernet_icon_disconnected_and_transition()
    test_ethernet_icon_hidden_without_ethernet()
    test_compose_network_snapshot_prefers_connected_ethernet()
    test_select_ethernet_ignores_wifi()
    test_build_ethernet_tooltip_connected_with_internet()
    test_build_ethernet_tooltip_connected_without_internet()
    test_build_ethernet_tooltip_disconnected()
    test_connectivity_snapshot_labels()
    test_toggle_ethernet_requests_connect_when_disconnected()
    test_toggle_ethernet_requests_disconnect_when_connected()
    test_connect_device_uses_activate_connection_dbus()
    test_disconnect_device_uses_device_disconnect_dbus()
    test_network_service_emits_on_refresh()
    test_network_service_reads_live_ethernet_fields()
    print("network safe tests OK")
