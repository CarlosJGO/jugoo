"""Mocked tests for NetworkManager hotspot helpers and NetworkService actions."""

from __future__ import annotations

import logging
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import GLib

from shell.eventbus import EventBus
from shell.models import (
    HotspotClientSnapshot,
    HotspotSnapshot,
    NetworkConnectivitySnapshot,
    NetworkInterfaceSnapshot,
    WifiHotspotCapabilities,
)
from shell.servicios.red.network import NetworkService, compose_network_snapshot
from shell.servicios.red.network_hotspot import (
    NM_802_11_MODE_AP,
    NM_WIFI_DEVICE_CAP_AP,
    NM_WIFI_DEVICE_CAP_FREQ_2GHZ,
    NM_WIFI_DEVICE_CAP_FREQ_5GHZ,
    NM_WIFI_DEVICE_CAP_FREQ_VALID,
    SHELL_HOTSPOT_CONNECTION_ID,
    assemble_hotspot_snapshot,
    band_from_nm,
    classify_dbus_hotspot_error,
    connection_is_hotspot,
    decode_wifi_capabilities,
    default_hotspot_ssid,
    hotspot_connection_settings_variant,
    ipv4_shared_from_settings,
    password_configured_from_settings,
    sanitize_hotspot_error_message,
    ssid_from_wireless_settings,
)


SECRET = "super-secret-psk-999"


def _iface(
    *,
    interface: str,
    device_type: str,
    state: str = "disconnected",
    device_path: str = "/org/freedesktop/NetworkManager/Devices/3",
) -> NetworkInterfaceSnapshot:
    return NetworkInterfaceSnapshot(
        interface=interface,
        device_type=device_type,
        state=state,
        connection_name="",
        ip_address="10.42.0.1" if state == "connected" and device_type == "wifi" else (
            "192.168.1.9" if state == "connected" else ""
        ),
        link_up=state == "connected",
        speed_mbps=1000 if device_type == "ethernet" and state == "connected" else None,
        mac_address="00:11:22:33:44:55",
        device_path=device_path,
    )


def _wifi(**kwargs) -> NetworkInterfaceSnapshot:
    return _iface(interface="wlan0", device_type="wifi", **kwargs)


def _ethernet(*, state: str = "connected") -> NetworkInterfaceSnapshot:
    return _iface(
        interface="enp42s0",
        device_type="ethernet",
        state=state,
        device_path="/org/freedesktop/NetworkManager/Devices/2",
    )


def _caps(*, ap: bool = True, band_24: bool = True, band_5: bool = True) -> WifiHotspotCapabilities:
    return WifiHotspotCapabilities(
        supports_ap=ap,
        supports_2_4ghz=band_24,
        supports_5ghz=band_5,
    )


def _assemble(**kwargs) -> HotspotSnapshot:
    defaults = dict(
        wifi=_wifi(),
        ethernet=_ethernet(),
        capabilities=_caps(),
        wifi_mode=2,
        ssid="CashlyOs-WiFi",
        band="auto",
        password_configured=True,
        ipv4_shared=True,
        clients=(),
        connection_path="",
        pending_enable=False,
        last_error_status="",
        last_error_message="",
        wireless_hardware_enabled=True,
    )
    defaults.update(kwargs)
    return assemble_hotspot_snapshot(**defaults)


class _LogTrap(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))
        try:
            self.messages.append(repr(record.args))
        except Exception:
            return

    def contains_secret(self) -> bool:
        return any(SECRET in message for message in self.messages)


def _install_log_trap() -> tuple[_LogTrap, logging.Logger]:
    trap = _LogTrap()
    logger = logging.getLogger("shell.servicios.red.network")
    logger.addHandler(trap)
    logger.setLevel(logging.DEBUG)
    return trap, logger


def test_decode_wifi_capabilities_from_live_adapter_flags() -> None:
    # wlan0 on this system reports WirelessCapabilities=3967
    caps = decode_wifi_capabilities(3967)
    assert caps.supports_ap is True
    assert caps.supports_2_4ghz is True
    assert caps.supports_5ghz is True


def test_decode_wifi_capabilities_without_ap_or_5ghz() -> None:
    flags = NM_WIFI_DEVICE_CAP_FREQ_VALID | NM_WIFI_DEVICE_CAP_FREQ_2GHZ
    caps = decode_wifi_capabilities(flags)
    assert caps.supports_ap is False
    assert caps.supports_2_4ghz is True
    assert caps.supports_5ghz is False
    ap_only_24 = decode_wifi_capabilities(
        NM_WIFI_DEVICE_CAP_AP | NM_WIFI_DEVICE_CAP_FREQ_VALID | NM_WIFI_DEVICE_CAP_FREQ_2GHZ,
    )
    assert ap_only_24.supports_ap is True
    assert ap_only_24.supports_5ghz is False


def test_hotspot_settings_variant_is_ap_shared_wpa() -> None:
    variant = hotspot_connection_settings_variant("Cafe-AP", SECRET, "auto", interface_name="wlan0")
    settings = variant.unpack()
    wireless = settings["802-11-wireless"]
    assert wireless["mode"] == "ap"
    assert wireless["band"] == "bg"
    assert settings["ipv4"]["method"] == "shared"
    assert settings["ipv6"]["method"] == "ignore"
    assert settings["connection"]["id"] == SHELL_HOTSPOT_CONNECTION_ID
    assert settings["connection"]["interface-name"] == "wlan0"
    assert settings["802-11-wireless-security"]["key-mgmt"] == "wpa-psk"
    assert list(settings["802-11-wireless-security"]["proto"]) == ["rsn"]
    assert list(settings["802-11-wireless-security"]["pairwise"]) == ["ccmp"]
    assert list(settings["802-11-wireless-security"]["group"]) == ["ccmp"]
    assert settings["802-11-wireless-security"]["psk"] == SECRET
    assert ssid_from_wireless_settings(wireless) == "Cafe-AP"


def test_hotspot_settings_variant_sets_nm_band() -> None:
    two = hotspot_connection_settings_variant("AP", SECRET, "2.4").unpack()
    five = hotspot_connection_settings_variant("AP", SECRET, "5").unpack()
    auto = hotspot_connection_settings_variant("AP", SECRET, "auto").unpack()
    assert auto["802-11-wireless"]["band"] == "bg"
    assert two["802-11-wireless"]["band"] == "bg"
    assert five["802-11-wireless"]["band"] == "a"
    assert band_from_nm("bg") == "2.4"
    assert band_from_nm("a") == "5"
    assert band_from_nm("") == "auto"


def test_connection_is_hotspot_requires_ap_mode() -> None:
    infra = {
        "connection": {"type": "802-11-wireless", "id": "Home"},
        "802-11-wireless": {"mode": "infrastructure"},
    }
    ap = {
        "connection": {"type": "802-11-wireless", "id": SHELL_HOTSPOT_CONNECTION_ID},
        "802-11-wireless": {"mode": "ap", "ssid": list(b"Cafe")},
        "ipv4": {"method": "shared"},
        "802-11-wireless-security": {"key-mgmt": "wpa-psk"},
    }
    assert connection_is_hotspot(infra) is False
    assert connection_is_hotspot(ap) is True
    assert ipv4_shared_from_settings(ap) is True
    assert password_configured_from_settings(ap) is True
    assert password_configured_from_settings(infra) is False


def test_assemble_states_for_missing_wifi_and_no_ap() -> None:
    missing = _assemble(wifi=None, capabilities=None)
    assert missing.status == "wifi_unavailable"
    assert missing.available is False
    unsupported = _assemble(capabilities=_caps(ap=False, band_24=True, band_5=False))
    assert unsupported.status == "ap_unsupported"
    assert unsupported.supports_ap is False


def test_assemble_sharing_requires_ethernet_upstream() -> None:
    sharing = _assemble(
        wifi=_wifi(state="connected"),
        ethernet=_ethernet(state="connected"),
        wifi_mode=NM_802_11_MODE_AP,
        ipv4_shared=True,
        clients=(HotspotClientSnapshot(mac_address="aa:bb:cc:dd:ee:ff"),),
    )
    assert sharing.active is True
    assert sharing.shared_connection is True
    assert sharing.status == "sharing"
    assert len(sharing.connected_clients) == 1

    no_up = _assemble(
        wifi=_wifi(state="connected"),
        ethernet=_ethernet(state="disconnected"),
        wifi_mode=NM_802_11_MODE_AP,
        ipv4_shared=True,
    )
    assert no_up.active is True
    assert no_up.shared_connection is False
    assert no_up.status == "active_no_upstream"

    active_not_shared = _assemble(
        wifi=_wifi(state="connected"),
        ethernet=_ethernet(state="connected"),
        wifi_mode=NM_802_11_MODE_AP,
        ipv4_shared=False,
    )
    assert active_not_shared.active is True
    assert active_not_shared.shared_connection is False
    assert active_not_shared.status == "active"


def test_assemble_starting_and_errors() -> None:
    starting = _assemble(pending_enable=True)
    assert starting.status == "starting"
    auth = _assemble(last_error_status="auth_error", last_error_message="Not authorized")
    assert auth.status == "auth_error"
    config = _assemble(last_error_status="config_error", last_error_message="bad")
    assert config.status == "config_error"
    off = _assemble()
    assert off.status == "off"
    assert off.active is False


def test_default_ssid_uses_hostname() -> None:
    assert default_hotspot_ssid("CashlyOs") == "CashlyOs-WiFi"


def test_dbus_error_classification_and_secret_sanitizer() -> None:
    assert classify_dbus_hotspot_error("NotAuthorized") == "auth_error"
    assert classify_dbus_hotspot_error("Access denied by polkit") == "auth_error"
    assert classify_dbus_hotspot_error("Invalid property") == "config_error"
    assert SECRET not in sanitize_hotspot_error_message(f"psk {SECRET} is invalid")


def _ready_service() -> NetworkService:
    service = NetworkService(EventBus())
    service._nm_proxy = object()
    service._bus = object()
    service._read_nm_hostname = lambda: "CashlyOs"
    service._schedule_refresh = lambda *_args, **_kwargs: None
    service._snapshot = compose_network_snapshot(
        (_ethernet(), _wifi(state="disconnected")),
        connectivity=NetworkConnectivitySnapshot(level="full"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
    )
    return service


def test_enable_creates_ap_profile_and_activates() -> None:
    service = _ready_service()
    created: list[tuple[str, str, str, str]] = []
    activated: list[tuple[str, str]] = []
    service._read_wifi_capabilities = lambda _path: _caps()
    service._find_hotspot_connection_path = lambda: ""
    service._add_hotspot_connection = (
        lambda ssid, password, band, *, interface_name: created.append(
            (ssid, password, band, interface_name),
        )
        or "/org/freedesktop/NetworkManager/Settings/9"
    )
    service._activate_hotspot_connection = (
        lambda path, device: activated.append((path, device)) or True
    )
    trap, logger = _install_log_trap()
    try:
        assert service._set_hotspot_enabled_idle(True, "Mi-PC-WiFi", SECRET, "auto") is False
    finally:
        logger.removeHandler(trap)
    assert created == [("Mi-PC-WiFi", SECRET, "auto", "wlan0")]
    assert activated == [
        (
            "/org/freedesktop/NetworkManager/Settings/9",
            "/org/freedesktop/NetworkManager/Devices/3",
        ),
    ]
    assert trap.contains_secret() is False


def test_disable_deactivates_hotspot_not_ethernet() -> None:
    service = _ready_service()
    calls: list[str] = []
    service._snapshot = compose_network_snapshot(
        (_ethernet(), _wifi(state="connected")),
        connectivity=NetworkConnectivitySnapshot(level="full"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
        hotspot=_assemble(wifi=_wifi(state="connected"), wifi_mode=NM_802_11_MODE_AP),
    )
    service._hotspot_active_connection_path = lambda: "/org/freedesktop/NetworkManager/ActiveConnection/4"
    service._disconnect_device = lambda path: calls.append(f"disconnect:{path}") or True
    nm = type("NM", (), {})()
    nm.calls: list[tuple[str, object]] = []

    def call_sync(method, parameters, *_args, **_kwargs):
        nm.calls.append((method, parameters.unpack()))
        return None

    nm.call_sync = call_sync
    service._nm_proxy = nm
    assert service._set_hotspot_enabled_idle(False, "", "", "auto") is False
    assert nm.calls == [("DeactivateConnection", ("/org/freedesktop/NetworkManager/ActiveConnection/4",))]
    assert calls == []


def test_apply_config_updates_ssid_and_band_without_logging_password() -> None:
    service = _ready_service()
    updates: list[tuple] = []
    service._find_hotspot_connection_path = lambda: "/org/freedesktop/NetworkManager/Settings/9"
    service._hotspot_password_configured = lambda _path: True
    service._update_hotspot_connection = (
        lambda path, ssid, password, band, *, interface_name: updates.append(
            (path, ssid, password, band, interface_name),
        )
        or True
    )
    trap, logger = _install_log_trap()
    try:
        assert service._apply_hotspot_config_idle("NuevoSSID", SECRET, "5") is False
    finally:
        logger.removeHandler(trap)
    assert updates == [
        (
            "/org/freedesktop/NetworkManager/Settings/9",
            "NuevoSSID",
            SECRET,
            "5",
            "wlan0",
        ),
    ]
    assert trap.contains_secret() is False


def test_enable_without_wifi_sets_config_error() -> None:
    service = _ready_service()
    service._snapshot = compose_network_snapshot(
        (_ethernet(),),
        connectivity=NetworkConnectivitySnapshot(level="full"),
        wireless_enabled=True,
        wireless_hardware_enabled=True,
    )
    service._schedule_refresh = lambda *_args, **_kwargs: None
    service._set_hotspot_enabled_idle(True, "AP", SECRET, "auto")
    assert service._hotspot_last_error_status == "config_error"
    assert "adaptador" in service._hotspot_last_error_message.lower()


def test_enable_without_ap_support() -> None:
    service = _ready_service()
    service._read_wifi_capabilities = lambda _path: _caps(ap=False, band_24=True, band_5=False)
    service._set_hotspot_enabled_idle(True, "AP", SECRET, "auto")
    assert "punto de acceso" in service._hotspot_last_error_message.lower()
    assert service._hotspot_pending_enable is False


def test_add_hotspot_connection_uses_settings_add_connection() -> None:
    service = _ready_service()
    calls: list[tuple[str, object]] = []

    class SettingsProxy:
        def call_sync(self, method, parameters, *_args, **_kwargs):
            calls.append((method, parameters.unpack()))
            return GLib.Variant("(o)", ("/org/freedesktop/NetworkManager/Settings/9",))

    real_new_sync = __import__("gi.repository", fromlist=["Gio"]).Gio.DBusProxy.new_sync

    def fake_new_sync(_bus, _flags, _info, _name, path, iface, _cancel):
        if path == "/org/freedesktop/NetworkManager/Settings":
            return SettingsProxy()
        return real_new_sync(_bus, _flags, _info, _name, path, iface, _cancel)

    trap, logger = _install_log_trap()
    with mock.patch("shell.servicios.red.network.Gio.DBusProxy.new_sync", side_effect=fake_new_sync):
        try:
            path = service._add_hotspot_connection(
                "Cafe-AP",
                SECRET,
                "2.4",
                interface_name="wlan0",
            )
        finally:
            logger.removeHandler(trap)
    assert path == "/org/freedesktop/NetworkManager/Settings/9"
    assert calls[0][0] == "AddConnection"
    payload = calls[0][1][0]
    assert payload["802-11-wireless"]["mode"] == "ap"
    assert payload["802-11-wireless"]["band"] == "bg"
    assert payload["ipv4"]["method"] == "shared"
    assert list(payload["802-11-wireless-security"]["proto"]) == ["rsn"]
    assert list(payload["802-11-wireless-security"]["pairwise"]) == ["ccmp"]
    assert list(payload["802-11-wireless-security"]["group"]) == ["ccmp"]
    assert payload["802-11-wireless-security"]["psk"] == SECRET
    assert trap.contains_secret() is False


def test_read_hotspot_clients_skips_active_access_point() -> None:
    service = _ready_service()
    service._read_wifi_mode = lambda _path: NM_802_11_MODE_AP

    class WirelessProxy:
        def get_cached_property(self, name):
            if name == "ActiveAccessPoint":
                return GLib.Variant("o", "/org/freedesktop/NetworkManager/AccessPoint/9")
            return None

        def call_sync(self, method, _params, *_args, **_kwargs):
            assert method == "GetAllAccessPoints"
            return GLib.Variant(
                "(ao)",
                (
                    [
                        "/org/freedesktop/NetworkManager/AccessPoint/9",
                        "/org/freedesktop/NetworkManager/AccessPoint/1",
                        "/org/freedesktop/NetworkManager/AccessPoint/2",
                    ],
                ),
            )

    class ApProxy:
        def __init__(self, mac: str) -> None:
            self.mac = mac

        def get_cached_property(self, name):
            if name == "HwAddress":
                return GLib.Variant("s", self.mac)
            return None

    def fake_new_sync(_bus, _flags, _info, _name, path, iface, _cancel):
        if iface.endswith("Device.Wireless"):
            return WirelessProxy()
        if path == "/org/freedesktop/NetworkManager/AccessPoint/1":
            return ApProxy("aa:aa:aa:aa:aa:01")
        if path == "/org/freedesktop/NetworkManager/AccessPoint/2":
            return ApProxy("aa:aa:aa:aa:aa:02")
        return ApProxy("")

    with mock.patch("shell.servicios.red.network.Gio.DBusProxy.new_sync", side_effect=fake_new_sync):
        clients = service._read_hotspot_clients("/org/freedesktop/NetworkManager/Devices/3")
    assert [item.mac_address for item in clients] == [
        "aa:aa:aa:aa:aa:01",
        "aa:aa:aa:aa:aa:02",
    ]


def test_activate_hotspot_maps_dbus_auth_error() -> None:
    service = _ready_service()
    service._read_wifi_capabilities = lambda _path: _caps()
    service._find_hotspot_connection_path = lambda: "/org/freedesktop/NetworkManager/Settings/9"
    service._hotspot_password_configured = lambda _path: True
    service._update_hotspot_connection = lambda *_args, **_kwargs: True

    class FakeError:
        def __init__(self) -> None:
            self.message = "NotAuthorized for this operation"

    def activate(_connection, _device):
        exc = FakeError()
        service._store_hotspot_dbus_error(exc)  # type: ignore[arg-type]
        service._hotspot_pending_enable = False
        return False

    service._activate_hotspot_connection = activate
    service._set_hotspot_enabled_idle(True, "AP", "", "auto")
    assert service._hotspot_last_error_status == "auth_error"


def test_hotspot_snapshot_never_includes_password() -> None:
    snapshot = _assemble()
    assert "password" not in snapshot.__dataclass_fields__
    assert not hasattr(snapshot, "psk")


def test_compose_network_snapshot_includes_hotspot() -> None:
    hotspot = _assemble(wifi=_wifi(state="connected"), wifi_mode=NM_802_11_MODE_AP)
    snapshot = compose_network_snapshot(
        (_ethernet(), _wifi(state="connected")),
        connectivity=NetworkConnectivitySnapshot(level="full"),
        hotspot=hotspot,
    )
    assert snapshot.hotspot.status in {"sharing", "active", "active_no_upstream"}
    assert snapshot.hotspot.wifi_device == "wlan0"


if __name__ == "__main__":
    test_decode_wifi_capabilities_from_live_adapter_flags()
    test_decode_wifi_capabilities_without_ap_or_5ghz()
    test_hotspot_settings_variant_is_ap_shared_wpa()
    test_hotspot_settings_variant_sets_nm_band()
    test_connection_is_hotspot_requires_ap_mode()
    test_assemble_states_for_missing_wifi_and_no_ap()
    test_assemble_sharing_requires_ethernet_upstream()
    test_assemble_starting_and_errors()
    test_default_ssid_uses_hostname()
    test_dbus_error_classification_and_secret_sanitizer()
    test_enable_creates_ap_profile_and_activates()
    test_disable_deactivates_hotspot_not_ethernet()
    test_apply_config_updates_ssid_and_band_without_logging_password()
    test_enable_without_wifi_sets_config_error()
    test_enable_without_ap_support()
    test_add_hotspot_connection_uses_settings_add_connection()
    test_read_hotspot_clients_skips_active_access_point()
    test_activate_hotspot_maps_dbus_auth_error()
    test_hotspot_snapshot_never_includes_password()
    test_compose_network_snapshot_includes_hotspot()
    print("network hotspot tests OK")
