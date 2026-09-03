"""NetworkManager-backed network monitoring for bar modules and future control center."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from ...config import (
    NETWORK_CONNECTIVITY_FOLLOWUP_DELAYS_MS,
    NETWORK_FALLBACK_POLL_SEC,
    NETWORK_REFRESH_DEBOUNCE_MS,
    NETWORK_WIFI_SCAN_FOLLOWUP_DELAYS_MS,
)
from ...eventbus import EventBus
from ...models import (
    HotspotClientSnapshot,
    HotspotSnapshot,
    NetworkConnectivitySnapshot,
    NetworkInterfaceSnapshot,
    NetworkSnapshot,
    WifiAccessPointSnapshot,
    WifiHotspotCapabilities,
)
from .network_hotspot import (
    HOTSPOT_PSK_MIN_LENGTH,
    NM_802_11_MODE_AP,
    NM_SETTINGS_CONNECTION_INTERFACE,
    NM_SETTINGS_INTERFACE,
    NM_SETTINGS_PATH,
    SHELL_HOTSPOT_CONNECTION_ID,
    assemble_hotspot_snapshot,
    band_from_nm,
    classify_dbus_hotspot_error,
    connection_is_hotspot,
    decode_wifi_capabilities,
    default_hotspot_ssid,
    hotspot_connection_settings_variant,
    ipv4_shared_from_settings,
    normalize_hotspot_ssid,
    password_configured_from_settings,
    sanitize_hotspot_error_message,
    ssid_from_wireless_settings,
    unpack_settings_map,
)

NETWORK_CHANGED = "network_changed"
NETWORK_ETHERNET_CLICKED = "network_ethernet_clicked"
NETWORK_DBUS_TIMEOUT_MS = 3_000
NETWORK_WIFI_CONNECT_TIMEOUT_MS = 15_000

NM_BUS_NAME = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH = "/org/freedesktop/NetworkManager"
NM_INTERFACE = "org.freedesktop.NetworkManager"
NM_DEVICE_INTERFACE = "org.freedesktop.NetworkManager.Device"
NM_DEVICE_WIRED_INTERFACE = "org.freedesktop.NetworkManager.Device.Wired"
NM_DEVICE_WIFI_INTERFACE = "org.freedesktop.NetworkManager.Device.Wireless"
NM_IP4_CONFIG_INTERFACE = "org.freedesktop.NetworkManager.IP4Config"
NM_ACTIVE_CONNECTION_INTERFACE = "org.freedesktop.NetworkManager.Connection.Active"
NM_ACCESS_POINT_INTERFACE = "org.freedesktop.NetworkManager.AccessPoint"
NM_DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"

NM_DEVICE_TYPE_ETHERNET = 1
NM_DEVICE_TYPE_WIFI = 2

NM_DEVICE_STATE_UNKNOWN = 0
NM_DEVICE_STATE_UNMANAGED = 10
NM_DEVICE_STATE_UNAVAILABLE = 20
NM_DEVICE_STATE_DISCONNECTED = 30
NM_DEVICE_STATE_PREPARE = 40
NM_DEVICE_STATE_CONFIG = 50
NM_DEVICE_STATE_NEED_AUTH = 60
NM_DEVICE_STATE_IP_CONFIG = 70
NM_DEVICE_STATE_IP_CHECK = 80
NM_DEVICE_STATE_SECONDARIES = 90
NM_DEVICE_STATE_ACTIVATED = 100
NM_DEVICE_STATE_DEACTIVATING = 110
NM_DEVICE_STATE_FAILED = 120

NM_CONNECTIVITY_UNKNOWN = 0
NM_CONNECTIVITY_NONE = 1
NM_CONNECTIVITY_PORTAL = 2
NM_CONNECTIVITY_LIMITED = 3
NM_CONNECTIVITY_FULL = 4

_DEVICE_TYPE_LABELS = {
    NM_DEVICE_TYPE_ETHERNET: "ethernet",
    NM_DEVICE_TYPE_WIFI: "wifi",
}

_CONNECTIVITY_LABELS = {
    NM_CONNECTIVITY_UNKNOWN: "unknown",
    NM_CONNECTIVITY_NONE: "none",
    NM_CONNECTIVITY_PORTAL: "portal",
    NM_CONNECTIVITY_LIMITED: "limited",
    NM_CONNECTIVITY_FULL: "full",
}

_STATE_LABELS = {
    NM_DEVICE_STATE_UNKNOWN: "unknown",
    NM_DEVICE_STATE_UNMANAGED: "unmanaged",
    NM_DEVICE_STATE_UNAVAILABLE: "unavailable",
    NM_DEVICE_STATE_DISCONNECTED: "disconnected",
    NM_DEVICE_STATE_PREPARE: "connecting",
    NM_DEVICE_STATE_CONFIG: "connecting",
    NM_DEVICE_STATE_NEED_AUTH: "connecting",
    NM_DEVICE_STATE_IP_CONFIG: "connecting",
    NM_DEVICE_STATE_IP_CHECK: "connecting",
    NM_DEVICE_STATE_SECONDARIES: "connecting",
    NM_DEVICE_STATE_ACTIVATED: "connected",
    NM_DEVICE_STATE_DEACTIVATING: "disconnecting",
    NM_DEVICE_STATE_FAILED: "failed",
}

_ETHERNET_CONNECTED_ICON = "network-wired-symbolic"
_ETHERNET_DISCONNECTED_ICON = "network-wired-disconnected-symbolic"
_ETHERNET_CONNECTING_ICON = "network-wired-acquiring-symbolic"
_ETHERNET_NO_INTERNET_ICON = "network-wired-no-route-symbolic"
_ETHERNET_UNAVAILABLE_ICON = "network-wired-no-route-symbolic"

_logger = logging.getLogger(__name__)


def _ipv4_forwarding_enabled() -> bool:
    try:
        return Path("/proc/sys/net/ipv4/ip_forward").read_text(encoding="ascii").strip() == "1"
    except (OSError, UnicodeError):
        return False


def device_state_label(state: int) -> str:
    return _STATE_LABELS.get(int(state), "unknown")


def device_type_label(device_type: int) -> str:
    return _DEVICE_TYPE_LABELS.get(int(device_type), "unknown")


def connectivity_label(connectivity: int) -> str:
    return _CONNECTIVITY_LABELS.get(int(connectivity), "unknown")


def read_manager_connectivity(
    nm_proxy: Gio.DBusProxy,
    *,
    force_check: bool = False,
) -> NetworkConnectivitySnapshot:
    """Read global connectivity from NetworkManager, optionally forcing a fresh check."""
    if force_check:
        try:
            result = nm_proxy.call_sync(
                "CheckConnectivity",
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            return NetworkConnectivitySnapshot.from_nm_label(
                connectivity_label(int(result.unpack()[0])),
            )
        except GLib.Error as exc:
            _logger.debug("CheckConnectivity failed: %s", exc.message)

    try:
        return NetworkConnectivitySnapshot.from_nm_label(
            connectivity_label(
                int(_cached_property(nm_proxy, "Connectivity", 0) or 0),
            ),
        )
    except GLib.Error:
        return NetworkConnectivitySnapshot(level="unknown")


def should_schedule_connectivity_followup(
    previous: NetworkSnapshot,
    current: NetworkSnapshot,
) -> bool:
    """Decide if a targeted connectivity re-check is needed after a snapshot change."""
    ethernet = current.ethernet
    if ethernet is None or not ethernet.connected:
        return False
    if current.connectivity.has_internet:
        return False

    previous_ethernet = previous.ethernet
    if previous_ethernet is None:
        return True
    if previous_ethernet.state != "connected" and ethernet.state == "connected":
        return True
    if previous.connectivity.has_internet and not current.connectivity.has_internet:
        return True
    if previous_ethernet.ip_address != ethernet.ip_address:
        return True
    if previous_ethernet.link_up != ethernet.link_up:
        return True
    return False


def format_link_speed(speed_mbps: int | None) -> str:
    if speed_mbps is None or speed_mbps <= 0:
        return ""
    if speed_mbps >= 1000 and speed_mbps % 1000 == 0:
        return f"{speed_mbps // 1000} Gbps"
    if speed_mbps >= 1000:
        return f"{speed_mbps / 1000:.1f} Gbps"
    return f"{speed_mbps} Mbps"


def ethernet_icon_name(snapshot: NetworkSnapshot) -> str | None:
    """Return the bar icon for the current ethernet snapshot, or None to hide the widget."""
    ethernet = snapshot.ethernet
    if ethernet is None:
        return None
    if ethernet.state in {"connecting", "disconnecting"}:
        return _ETHERNET_CONNECTING_ICON
    if ethernet.state == "unavailable":
        return _ETHERNET_UNAVAILABLE_ICON
    if ethernet.state == "connected":
        if snapshot.connectivity.has_internet:
            return _ETHERNET_CONNECTED_ICON
        return _ETHERNET_NO_INTERNET_ICON
    return _ETHERNET_DISCONNECTED_ICON


def ethernet_visual_state(snapshot: NetworkSnapshot) -> str | None:
    """Compact visual state used by the bar icon colors and connecting pulse."""
    ethernet = snapshot.ethernet
    if ethernet is None:
        return None
    if ethernet.state in {"connecting", "disconnecting"}:
        return "connecting"
    if ethernet.state == "unavailable":
        return "unavailable"
    if ethernet.state == "connected":
        if snapshot.connectivity.has_internet:
            return "connected"
        return "no-internet"
    return "disconnected"


def connectivity_state_label(state: str) -> str:
    labels = {
        "connected": "Conectado",
        "connecting": "Conectando",
        "disconnecting": "Desconectando",
        "disconnected": "Desconectado",
        "unavailable": "No disponible",
        "unmanaged": "No gestionado",
        "failed": "Error",
        "unknown": "Desconocido",
    }
    return labels.get(state, state.capitalize())


def build_ethernet_tooltip(snapshot: NetworkSnapshot) -> str:
    ethernet = snapshot.ethernet
    if ethernet is None:
        return "Ethernet\nNo disponible"

    lines = ["Ethernet"]
    if ethernet.state == "connected":
        lines.append(f"Conectado · {snapshot.connectivity.summary_label}")
    else:
        lines.append(connectivity_state_label(ethernet.state))

    if ethernet.state == "connected":
        if ethernet.ip_address:
            lines.append(f"IP: {ethernet.ip_address}")
        if ethernet.interface:
            lines.append(f"Interfaz: {ethernet.interface}")
        speed = format_link_speed(ethernet.speed_mbps)
        if speed:
            lines.append(f"Velocidad: {speed}")
    elif ethernet.interface and ethernet.state not in {"unknown", "unmanaged"}:
        lines.append(f"Interfaz: {ethernet.interface}")

    return "\n".join(lines)


def select_primary_interface(
    interfaces: tuple[NetworkInterfaceSnapshot, ...],
) -> NetworkInterfaceSnapshot | None:
    if not interfaces:
        return None

    def rank(item: NetworkInterfaceSnapshot) -> tuple[int, int, str]:
        state_rank = {
            "connected": 0,
            "connecting": 1,
            "disconnecting": 2,
            "disconnected": 3,
            "failed": 4,
            "unavailable": 5,
            "unmanaged": 6,
            "unknown": 7,
        }
        type_rank = 0 if item.device_type == "ethernet" else 1
        return (state_rank.get(item.state, 8), type_rank, item.interface)

    return min(interfaces, key=rank)


def select_ethernet_interface(
    interfaces: tuple[NetworkInterfaceSnapshot, ...],
) -> NetworkInterfaceSnapshot | None:
    ethernet = tuple(item for item in interfaces if item.device_type == "ethernet")
    return select_primary_interface(ethernet)


def select_wifi_interface(
    interfaces: tuple[NetworkInterfaceSnapshot, ...],
) -> NetworkInterfaceSnapshot | None:
    wifi = tuple(item for item in interfaces if item.device_type == "wifi")
    return select_primary_interface(wifi)


def compose_network_snapshot(
    interfaces: tuple[NetworkInterfaceSnapshot, ...],
    *,
    connectivity: NetworkConnectivitySnapshot,
    wireless_enabled: bool = False,
    wireless_hardware_enabled: bool = True,
    wifi_access_points: tuple[WifiAccessPointSnapshot, ...] = (),
    wifi_connection_target: str = "",
    wifi_connection_error: str = "",
    hotspot: HotspotSnapshot | None = None,
) -> NetworkSnapshot:
    ethernet = select_ethernet_interface(interfaces)
    wifi = select_wifi_interface(interfaces)
    primary = select_primary_interface(interfaces)
    return NetworkSnapshot(
        ethernet=ethernet,
        wifi=wifi,
        primary=primary,
        connectivity=connectivity,
        wireless_enabled=wireless_enabled,
        wireless_hardware_enabled=wireless_hardware_enabled,
        wifi_access_points=wifi_access_points,
        wifi_connection_target=wifi_connection_target,
        wifi_connection_error=wifi_connection_error,
        hotspot=hotspot if hotspot is not None else HotspotSnapshot.empty(),
    )


def _variant_value(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value


def _variant_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, GLib.Variant):
        return {str(key): _variant_value(item) for key, item in value.unpack()}
    if isinstance(value, dict):
        return {str(key): _variant_value(item) for key, item in value.items()}
    return {}


def _cached_property(proxy: Gio.DBusProxy, name: str, default: Any = None) -> Any:
    variant = proxy.get_cached_property(name)
    if variant is None:
        return default
    return variant.unpack()


def _decode_ssid(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace").strip()
    if isinstance(raw, list):
        return bytes(raw).decode("utf-8", errors="replace").strip()
    return str(raw).strip()


def read_wireless_radio_state(nm_proxy: Gio.DBusProxy) -> tuple[bool, bool]:
    enabled = bool(_cached_property(nm_proxy, "WirelessEnabled", False))
    hardware = bool(_cached_property(nm_proxy, "WirelessHardwareEnabled", False))
    return enabled, hardware


def wifi_scan_allowed(snapshot: NetworkSnapshot) -> bool:
    """Return True when NetworkManager allows RequestScan on the Wi-Fi device."""
    if not snapshot.wireless_hardware_enabled or not snapshot.wireless_enabled:
        return False
    wifi = snapshot.wifi
    if wifi is None or not wifi.device_path:
        return False
    if snapshot.hotspot.active or snapshot.hotspot.status == "starting":
        return False
    return wifi.state not in ("unavailable", "unmanaged", "unknown")


def read_access_point_snapshot(
    bus: Gio.DBusConnection,
    access_point_path: str,
    *,
    active_path: str = "/",
) -> WifiAccessPointSnapshot | None:
    if not access_point_path or access_point_path == "/":
        return None
    try:
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            access_point_path,
            NM_ACCESS_POINT_INTERFACE,
            None,
        )
    except GLib.Error:
        return None

    ssid = _decode_ssid(_cached_property(proxy, "Ssid", b""))
    if not ssid:
        return None
    wpa_flags = int(_cached_property(proxy, "WpaFlags", 0) or 0)
    rsn_flags = int(_cached_property(proxy, "RsnFlags", 0) or 0)
    return WifiAccessPointSnapshot(
        path=access_point_path,
        ssid=ssid,
        strength=int(_cached_property(proxy, "Strength", 0) or 0),
        secured=bool(wpa_flags or rsn_flags),
        frequency_mhz=int(_cached_property(proxy, "Frequency", 0) or 0),
        active=access_point_path == active_path,
    )


def read_wifi_access_points(
    bus: Gio.DBusConnection,
    device_path: str,
) -> tuple[WifiAccessPointSnapshot, ...]:
    try:
        wireless_proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            device_path,
            NM_DEVICE_WIFI_INTERFACE,
            None,
        )
        result = wireless_proxy.call_sync(
            "GetAllAccessPoints",
            None,
            Gio.DBusCallFlags.NONE,
            NETWORK_DBUS_TIMEOUT_MS,
            None,
        )
        access_point_paths = tuple(str(path) for path in result.unpack()[0])
        active_path = str(_cached_property(wireless_proxy, "ActiveAccessPoint", "/") or "/")
    except GLib.Error as exc:
        _logger.debug("GetAllAccessPoints failed for %s: %s", device_path, exc.message)
        return ()

    points: list[WifiAccessPointSnapshot] = []
    for path in access_point_paths:
        snapshot = read_access_point_snapshot(
            bus,
            path,
            active_path=active_path,
        )
        if snapshot is not None:
            points.append(snapshot)
    points.sort(key=lambda item: (-item.strength, item.ssid.lower()))
    return tuple(points)


def _wifi_connection_settings_variant(ssid: str, password: str) -> GLib.Variant:
    sections: list[tuple[str, dict[str, GLib.Variant]]] = [
        (
            "connection",
            {
                "type": GLib.Variant("s", "802-11-wireless"),
                "id": GLib.Variant("s", ssid),
                "autoconnect": GLib.Variant("b", True),
            },
        ),
        (
            "802-11-wireless",
            {
                "ssid": GLib.Variant("ay", ssid.encode("utf-8")),
                "mode": GLib.Variant("s", "infrastructure"),
            },
        ),
        ("ipv4", {"method": GLib.Variant("s", "auto")}),
        ("ipv6", {"method": GLib.Variant("s", "auto")}),
    ]
    if password:
        sections.append(
            (
                "802-11-wireless-security",
                {
                    "key-mgmt": GLib.Variant("s", "wpa-psk"),
                    "psk": GLib.Variant("s", password),
                },
            ),
        )

    return GLib.Variant("a{sa{sv}}", sections)


def _wifi_settings_with_password(
    settings: dict[str, Any],
    password: str,
) -> GLib.Variant:
    updated_settings = dict(settings)
    security = settings.get("802-11-wireless-security", {})
    if isinstance(security, GLib.Variant):
        security = security.unpack()
    security = dict(security) if isinstance(security, dict) else {}
    security["key-mgmt"] = GLib.Variant("s", "wpa-psk")
    security["psk"] = GLib.Variant("s", password)
    updated_settings["802-11-wireless-security"] = security
    return GLib.Variant("a{sa{sv}}", updated_settings)


def _primary_ipv4_from_ip4_config(
    bus: Gio.DBusConnection,
    ip4_config_path: str,
) -> str:
    if not ip4_config_path or ip4_config_path == "/":
        return ""
    try:
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            ip4_config_path,
            NM_IP4_CONFIG_INTERFACE,
            None,
        )
        address_data = _cached_property(proxy, "AddressData", ())
        for entry in address_data:
            if not isinstance(entry, dict):
                entry = _variant_mapping(entry)
            address = str(entry.get("address") or "").strip()
            if address:
                return address
    except GLib.Error:
        return ""
    return ""


def _connection_name_for_device(
    bus: Gio.DBusConnection,
    active_connection_path: str,
) -> str:
    if not active_connection_path or active_connection_path == "/":
        return ""
    try:
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            active_connection_path,
            NM_ACTIVE_CONNECTION_INTERFACE,
            None,
        )
        return str(_cached_property(proxy, "Id", "") or "").strip()
    except GLib.Error:
        return ""


def _wired_speed_mbps(
    bus: Gio.DBusConnection,
    device_path: str,
) -> int | None:
    try:
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            device_path,
            NM_DEVICE_WIRED_INTERFACE,
            None,
        )
        speed = int(_cached_property(proxy, "Speed", 0) or 0)
        return speed if speed > 0 else None
    except GLib.Error:
        return None


def _wired_carrier(
    bus: Gio.DBusConnection,
    device_path: str,
) -> bool:
    try:
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            device_path,
            NM_DEVICE_WIRED_INTERFACE,
            None,
        )
        return bool(_cached_property(proxy, "Carrier", False))
    except GLib.Error:
        return False


def read_interface_snapshot(
    bus: Gio.DBusConnection,
    device_path: str,
) -> NetworkInterfaceSnapshot | None:
    try:
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            device_path,
            NM_DEVICE_INTERFACE,
            None,
        )
    except GLib.Error:
        return None

    device_type = int(_cached_property(proxy, "DeviceType", 0) or 0)
    type_label = device_type_label(device_type)
    if type_label not in {"ethernet", "wifi"}:
        return None

    state = int(_cached_property(proxy, "State", 0) or 0)
    interface = str(_cached_property(proxy, "Interface", "") or "").strip()
    mac_address = str(_cached_property(proxy, "HwAddress", "") or "").strip()
    active_connection = str(_cached_property(proxy, "ActiveConnection", "/") or "/")
    ip4_config = str(_cached_property(proxy, "Ip4Config", "/") or "/")

    link_up = False
    speed_mbps: int | None = None
    if type_label == "ethernet":
        link_up = _wired_carrier(bus, device_path)
        speed_mbps = _wired_speed_mbps(bus, device_path)
    elif state == NM_DEVICE_STATE_ACTIVATED:
        link_up = True

    return NetworkInterfaceSnapshot(
        interface=interface,
        device_type=type_label,
        state=device_state_label(state),
        connection_name=_connection_name_for_device(bus, active_connection),
        ip_address=_primary_ipv4_from_ip4_config(bus, ip4_config),
        link_up=link_up,
        speed_mbps=speed_mbps,
        mac_address=mac_address,
        device_path=device_path,
    )


class _DeviceWatcher:
    """Tracks one NetworkManager device and notifies the service on changes."""

    def __init__(
        self,
        bus: Gio.DBusConnection,
        device_path: str,
        on_change: Callable[[], None],
    ) -> None:
        self._device_path = device_path
        self._on_change = on_change
        self._subscriptions: list[tuple[Gio.DBusProxy, int]] = []
        self._proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            NM_BUS_NAME,
            device_path,
            NM_DEVICE_INTERFACE,
            None,
        )
        for prop in (
            "State",
            "ActiveConnection",
            "Ip4Config",
            "Interface",
            "Managed",
            "Ip4Connectivity",
        ):
            self._bind(self._proxy, f"notify::{prop}", self._emit_change)
        self._bind(self._proxy, "g-signal", self._on_signal)
        if int(_cached_property(self._proxy, "DeviceType", 0) or 0) == NM_DEVICE_TYPE_ETHERNET:
            self._watch_wired_properties(bus)
        elif int(_cached_property(self._proxy, "DeviceType", 0) or 0) == NM_DEVICE_TYPE_WIFI:
            self._watch_wireless_properties(bus)

    @property
    def device_path(self) -> str:
        return self._device_path

    def _bind(
        self,
        proxy: Gio.DBusProxy,
        signal: str,
        callback: Callable[..., Any],
    ) -> None:
        self._subscriptions.append((proxy, proxy.connect(signal, callback)))

    def _watch_wired_properties(self, bus: Gio.DBusConnection) -> None:
        try:
            wired_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                self._device_path,
                NM_DEVICE_WIRED_INTERFACE,
                None,
            )
        except GLib.Error:
            return
        for prop in ("Carrier", "Speed"):
            self._bind(wired_proxy, f"notify::{prop}", self._emit_change)

    def _watch_wireless_properties(self, bus: Gio.DBusConnection) -> None:
        try:
            wireless_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                self._device_path,
                NM_DEVICE_WIFI_INTERFACE,
                None,
            )
        except GLib.Error:
            return
        for prop in ("AccessPoints", "ActiveAccessPoint", "LastScan", "Mode"):
            self._bind(wireless_proxy, f"notify::{prop}", self._emit_change)
        self._bind(wireless_proxy, "g-signal", self._on_signal)

    def disconnect(self) -> None:
        for proxy, handler_id in self._subscriptions:
            proxy.disconnect(handler_id)
        self._subscriptions.clear()

    def _emit_change(self, *_args) -> None:
        self._on_change()

    def _on_signal(
        self,
        _proxy: Gio.DBusProxy,
        _sender: str,
        signal_name: str,
        _parameters: GLib.Variant,
    ) -> None:
        if signal_name in {"StateChanged", "PropertiesChanged"}:
            self._on_change()


class NetworkService:
    """Single source of truth for network state via NetworkManager D-Bus signals."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._snapshot = NetworkSnapshot.empty()
        self._started = False
        self._bus: Gio.DBusConnection | None = None
        self._nm_proxy: Gio.DBusProxy | None = None
        self._device_watchers: dict[str, _DeviceWatcher] = {}
        self._refresh_source_id = 0
        self._fallback_source_id = 0
        self._connectivity_followup_source_id = 0
        self._connectivity_followup_delays: list[int] = []
        self._wifi_scan_followup_source_id = 0
        self._wifi_scan_followup_delays: list[int] = []
        self._wifi_scan_pending = False
        self._wifi_connection_target = ""
        self._wifi_connection_error = ""
        self._wifi_connection_timeout_source_id = 0
        self._hotspot_pending_enable = False
        self._hotspot_last_error_status = ""
        self._hotspot_last_error_message = ""
        self._hotspot_activate_source_id = 0
        self._hotspot_activate_delays: list[int] = []
        self._nm_handler_ids: list[int] = []

    @property
    def snapshot(self) -> NetworkSnapshot:
        return self._snapshot

    @property
    def available(self) -> bool:
        return self._nm_proxy is not None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if not self._connect_network_manager():
            self._schedule_fallback_poll()
            return
        self._scan_devices()
        self._refresh_snapshot(emit=True)

    def close(self) -> None:
        self._started = False
        self._cancel_refresh()
        self._cancel_connectivity_followup()
        self._cancel_wifi_scan_followup()
        self._cancel_wifi_connection_timeout()
        self._cancel_hotspot_activate_retry()
        if self._fallback_source_id:
            GLib.source_remove(self._fallback_source_id)
            self._fallback_source_id = 0
        for watcher in self._device_watchers.values():
            watcher.disconnect()
        self._device_watchers.clear()
        if self._nm_proxy is not None:
            for handler_id in self._nm_handler_ids:
                self._nm_proxy.disconnect(handler_id)
            self._nm_handler_ids.clear()
            self._nm_proxy = None
        self._bus = None

    def refresh(self) -> None:
        if self._nm_proxy is None:
            return
        self._scan_devices()
        self._refresh_snapshot(emit=True)

    def toggle_ethernet(self) -> None:
        """Request connect/disconnect for the primary ethernet device via D-Bus."""
        GLib.idle_add(self._toggle_ethernet_idle)

    def set_wireless_enabled(self, enabled: bool) -> None:
        GLib.idle_add(self._set_wireless_enabled_idle, enabled)

    def request_wifi_scan(self) -> None:
        GLib.idle_add(self._request_wifi_scan_idle)

    def connect_wifi(self, access_point_path: str, password: str = "") -> None:
        GLib.idle_add(self._connect_wifi_idle, access_point_path, password)

    def disconnect_wifi(self) -> None:
        GLib.idle_add(self._disconnect_wifi_idle)

    def set_hotspot_enabled(
        self,
        enabled: bool,
        *,
        ssid: str = "",
        password: str = "",
        band: str = "auto",
    ) -> None:
        GLib.idle_add(self._set_hotspot_enabled_idle, enabled, ssid, password, band)

    def apply_hotspot_config(self, ssid: str, password: str, band: str) -> None:
        GLib.idle_add(self._apply_hotspot_config_idle, ssid, password, band)

    def _wifi_device_path(self) -> str | None:
        wifi = self._snapshot.wifi
        if wifi is None or not wifi.device_path:
            return None
        return wifi.device_path

    def _set_wireless_enabled_idle(self, enabled: bool) -> bool:
        if self._bus is None or self._nm_proxy is None:
            return False
        try:
            self._bus.call_sync(
                NM_BUS_NAME,
                NM_OBJECT_PATH,
                NM_DBUS_PROPERTIES,
                "Set",
                GLib.Variant(
                    "(ssv)",
                    (
                        NM_INTERFACE,
                        "WirelessEnabled",
                        GLib.Variant("b", enabled),
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
            )
            if enabled:
                self._schedule_wifi_scan_when_ready()
            else:
                self._cancel_wifi_scan_followup()
            self._schedule_refresh()
        except GLib.Error as exc:
            _logger.warning("Failed to set WirelessEnabled=%s: %s", enabled, exc.message)
        return False

    def _request_wifi_scan_idle(self) -> bool:
        if not wifi_scan_allowed(self._snapshot):
            _logger.debug("Skipping Wi-Fi scan: interface not ready")
            return False
        device_path = self._wifi_device_path()
        if self._bus is None or device_path is None:
            return False
        try:
            wireless_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_WIFI_INTERFACE,
                None,
            )
            wireless_proxy.call_sync(
                "RequestScan",
                GLib.Variant("(a{sv})", ({},)),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            self._wifi_scan_pending = False
            self._schedule_wifi_scan_followup()
        except GLib.Error as exc:
            if "Scanning not allowed while unavailable" in exc.message:
                _logger.debug("Wi-Fi scan deferred: %s", exc.message)
                self._schedule_wifi_scan_when_ready()
            else:
                _logger.warning("RequestScan failed: %s", exc.message)
        return False

    def _connect_wifi_idle(self, access_point_path: str, password: str) -> bool:
        access_point = None
        if self._bus is not None:
            access_point = read_access_point_snapshot(self._bus, access_point_path)
        if access_point is not None:
            self._wifi_connection_target = access_point.ssid
            self._wifi_connection_error = ""
            self._cancel_wifi_connection_timeout()
            self._schedule_refresh()
        if self._connect_wifi_access_point(access_point_path, password):
            self._wifi_connection_timeout_source_id = GLib.timeout_add(
                NETWORK_WIFI_CONNECT_TIMEOUT_MS,
                self._wifi_connection_timeout,
            )
            self._schedule_connectivity_followup(trigger="wifi-connect")
        else:
            if access_point is not None:
                self._wifi_connection_error = (
                    f"No se pudo conectar a {access_point.ssid}. Comprueba la contraseña."
                )
            self._schedule_refresh()
        return False

    def _disconnect_wifi_idle(self) -> bool:
        device_path = self._wifi_device_path()
        if device_path is None:
            return False
        self._disconnect_device(device_path)
        self._cancel_wifi_connection_timeout()
        self._wifi_connection_target = ""
        self._wifi_connection_error = ""
        self._schedule_refresh()
        return False

    def _connect_wifi_access_point(self, access_point_path: str, password: str) -> bool:
        if self._bus is None or self._nm_proxy is None:
            return False
        device_path = self._wifi_device_path()
        if device_path is None:
            return False

        ap = read_access_point_snapshot(self._bus, access_point_path)
        if ap is None:
            return False

        try:
            device_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_INTERFACE,
                None,
            )
            available = _cached_property(device_proxy, "AvailableConnections", ())
            for connection_path in available:
                settings_proxy = Gio.DBusProxy.new_sync(
                    self._bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    NM_BUS_NAME,
                    str(connection_path),
                    "org.freedesktop.NetworkManager.Settings.Connection",
                    None,
                )
                settings = settings_proxy.call_sync(
                    "GetSettings",
                    None,
                    Gio.DBusCallFlags.NONE,
                    NETWORK_DBUS_TIMEOUT_MS,
                    None,
                ).unpack()[0]
                wireless = settings.get("802-11-wireless", {})
                ssid_value = wireless.get("ssid")
                if isinstance(ssid_value, GLib.Variant):
                    ssid_value = ssid_value.unpack()
                ssid = _decode_ssid(ssid_value)
                if ssid == ap.ssid:
                    if password:
                        settings_proxy.call_sync(
                            "Update",
                            GLib.Variant.new_tuple(
                                _wifi_settings_with_password(settings, password),
                            ),
                            Gio.DBusCallFlags.NONE,
                            NETWORK_DBUS_TIMEOUT_MS,
                            None,
                        )
                    self._nm_proxy.call_sync(
                        "ActivateConnection",
                        GLib.Variant(
                            "(ooo)",
                            (str(connection_path), device_path, access_point_path),
                        ),
                        Gio.DBusCallFlags.NONE,
                        NETWORK_DBUS_TIMEOUT_MS,
                        None,
                    )
                    return True

            if ap.secured and not password:
                _logger.info("Wi-Fi network %s requires a password", ap.ssid)
                return False

            self._nm_proxy.call_sync(
                "AddAndActivateConnection",
                GLib.Variant(
                    "(a{sa{sv}}oo)",
                    (
                        _wifi_connection_settings_variant(ap.ssid, password),
                        device_path,
                        "/",
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            _logger.warning(
                "Failed to connect to Wi-Fi access point %s: %s",
                access_point_path,
                exc.message,
            )
            return False

    def _set_hotspot_enabled_idle(
        self,
        enabled: bool,
        ssid: str,
        password: str,
        band: str,
    ) -> bool:
        if enabled:
            self._hotspot_pending_enable = True
            self._hotspot_activate_delays = list(NETWORK_WIFI_SCAN_FOLLOWUP_DELAYS_MS)
            self._clear_hotspot_error()
            if not self._ensure_wireless_radio():
                self._hotspot_pending_enable = False
                self._schedule_refresh()
                return False
            if not self._activate_hotspot(ssid=ssid, password=password, band=band):
                self._schedule_hotspot_activate_retry(ssid, password, band)
        else:
            self._hotspot_pending_enable = False
            self._cancel_hotspot_activate_retry()
            self._clear_hotspot_error()
            self._deactivate_hotspot()
        self._schedule_refresh()
        return False

    def _cancel_wifi_connection_timeout(self) -> None:
        if self._wifi_connection_timeout_source_id:
            GLib.source_remove(self._wifi_connection_timeout_source_id)
            self._wifi_connection_timeout_source_id = 0

    def _wifi_connection_timeout(self) -> bool:
        self._wifi_connection_timeout_source_id = 0
        wifi = self._snapshot.wifi
        if wifi is not None and wifi.connected:
            return False
        if self._wifi_connection_target:
            self._wifi_connection_error = (
                f"No se pudo conectar a {self._wifi_connection_target} en 15 segundos. "
                "Comprueba la contraseña y vuelve a intentarlo."
            )
            self._schedule_refresh()
        return False

    def _apply_hotspot_config_idle(self, ssid: str, password: str, band: str) -> bool:
        self._clear_hotspot_error()
        connection_path = self._update_or_create_hotspot_profile(ssid, password, band)
        if not connection_path:
            self._schedule_refresh()
            return False
        if self._snapshot.hotspot.active or self._hotspot_pending_enable:
            wifi = self._snapshot.wifi
            if wifi is not None and wifi.device_path:
                active = self._hotspot_active_connection_path()
                if active and self._nm_proxy is not None:
                    try:
                        self._nm_proxy.call_sync(
                            "DeactivateConnection",
                            GLib.Variant("(o)", (active,)),
                            Gio.DBusCallFlags.NONE,
                            NETWORK_DBUS_TIMEOUT_MS,
                            None,
                        )
                    except GLib.Error as exc:
                        self._store_hotspot_dbus_error(exc)
                self._hotspot_pending_enable = True
                self._activate_hotspot_connection(connection_path, wifi.device_path)
        self._schedule_refresh()
        return False

    def _ensure_wireless_radio(self) -> bool:
        if self._bus is None or self._nm_proxy is None:
            self._store_hotspot_error("NetworkManager no está disponible")
            return False
        if not self._snapshot.wireless_hardware_enabled:
            self._store_hotspot_error("El hardware Wi-Fi está desactivado")
            return False
        if not self._snapshot.wireless_enabled:
            self._set_wireless_enabled_idle(True)
        return True

    def _activate_hotspot(self, *, ssid: str, password: str, band: str) -> bool:
        """Return False if the radio/device is not ready yet and a retry is useful."""
        wifi = self._snapshot.wifi
        if wifi is None or not wifi.device_path:
            self._store_hotspot_error("No hay un adaptador Wi-Fi gestionado")
            self._hotspot_pending_enable = False
            return True
        capabilities = self._read_wifi_capabilities(wifi.device_path)
        if capabilities is None or not capabilities.supports_ap:
            self._store_hotspot_error("Este adaptador Wi-Fi no admite el modo punto de acceso")
            self._hotspot_pending_enable = False
            return True
        if band == "5" and not capabilities.supports_5ghz:
            self._store_hotspot_error("Este adaptador no admite 5 GHz")
            self._hotspot_pending_enable = False
            return True
        if wifi.state in {"unavailable", "unmanaged", "unknown"}:
            return False
        connection_path = self._update_or_create_hotspot_profile(ssid, password, band)
        if not connection_path:
            return True
        self._activate_hotspot_connection(connection_path, wifi.device_path)
        return True

    def _update_or_create_hotspot_profile(
        self,
        ssid: str,
        password: str,
        band: str,
    ) -> str:
        if self._bus is None:
            self._store_hotspot_error("NetworkManager no está disponible")
            return ""
        wifi = self._snapshot.wifi
        interface_name = wifi.interface if wifi is not None else ""
        hostname = self._read_nm_hostname()
        resolved_ssid = normalize_hotspot_ssid(ssid, fallback=default_hotspot_ssid(hostname))
        if not resolved_ssid:
            self._store_hotspot_error("El nombre del punto de acceso no es válido")
            self._hotspot_pending_enable = False
            return ""
        connection_path = self._find_hotspot_connection_path()
        if not connection_path:
            if len(password) < HOTSPOT_PSK_MIN_LENGTH:
                self._store_hotspot_error("La contraseña debe tener al menos 8 caracteres")
                self._hotspot_pending_enable = False
                return ""
            return self._add_hotspot_connection(
                resolved_ssid,
                password,
                band,
                interface_name=interface_name,
            )
        if password and len(password) < HOTSPOT_PSK_MIN_LENGTH:
            self._store_hotspot_error("La contraseña debe tener al menos 8 caracteres")
            self._hotspot_pending_enable = False
            return ""
        if not password and not self._hotspot_password_configured(connection_path):
            self._store_hotspot_error("La contraseña debe tener al menos 8 caracteres")
            self._hotspot_pending_enable = False
            return ""
        if not self._update_hotspot_connection(
            connection_path,
            resolved_ssid,
            password,
            band,
            interface_name=interface_name,
        ):
            return ""
        return connection_path

    def _add_hotspot_connection(
        self,
        ssid: str,
        password: str,
        band: str,
        *,
        interface_name: str,
    ) -> str:
        if self._bus is None:
            return ""
        try:
            settings_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                NM_SETTINGS_PATH,
                NM_SETTINGS_INTERFACE,
                None,
            )
            result = settings_proxy.call_sync(
                "AddConnection",
                GLib.Variant.new_tuple(
                    hotspot_connection_settings_variant(
                        ssid,
                        password,
                        band,
                        interface_name=interface_name,
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            path = str(result.unpack()[0])
            _logger.info(
                "Created NetworkManager hotspot profile %s ssid=%s band=%s",
                path,
                ssid,
                band,
            )
            return path
        except GLib.Error as exc:
            self._store_hotspot_dbus_error(exc)
            return ""

    def _update_hotspot_connection(
        self,
        connection_path: str,
        ssid: str,
        password: str,
        band: str,
        *,
        interface_name: str,
    ) -> bool:
        settings = self._get_connection_settings(connection_path)
        if settings is None:
            self._store_hotspot_error("No se pudo leer el perfil del punto de acceso")
            return False
        connection = unpack_settings_map(settings.get("connection"))
        uuid = str(connection.get("uuid") or "").strip()
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                connection_path,
                NM_SETTINGS_CONNECTION_INTERFACE,
                None,
            )
            proxy.call_sync(
                "Update",
                GLib.Variant.new_tuple(
                    hotspot_connection_settings_variant(
                        ssid,
                        password,
                        band,
                        interface_name=interface_name,
                        uuid=uuid,
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            _logger.info("Updated NetworkManager hotspot profile ssid=%s band=%s", ssid, band)
            return True
        except GLib.Error as exc:
            self._store_hotspot_dbus_error(exc)
            return False

    def _activate_hotspot_connection(self, connection_path: str, device_path: str) -> bool:
        if self._nm_proxy is None:
            return False
        try:
            self._nm_proxy.call_sync(
                "ActivateConnection",
                GLib.Variant("(ooo)", (connection_path, device_path, "/")),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            _logger.info("Requested hotspot activation on %s", device_path)
            return True
        except GLib.Error as exc:
            self._store_hotspot_dbus_error(exc)
            self._hotspot_pending_enable = False
            return False

    def _deactivate_hotspot(self) -> None:
        if self._bus is None or self._nm_proxy is None:
            return
        active_path = self._hotspot_active_connection_path()
        if not active_path:
            return
        try:
            self._nm_proxy.call_sync(
                "DeactivateConnection",
                GLib.Variant("(o)", (active_path,)),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            _logger.info("Requested hotspot deactivation")
        except GLib.Error as exc:
            self._store_hotspot_dbus_error(exc)

    def _hotspot_active_connection_path(self) -> str:
        wifi = self._snapshot.wifi
        if self._bus is None or wifi is None or not wifi.device_path:
            return ""
        try:
            device_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                wifi.device_path,
                NM_DEVICE_INTERFACE,
                None,
            )
            active = str(_cached_property(device_proxy, "ActiveConnection", "/") or "/")
        except GLib.Error:
            return ""
        if active in {"", "/"}:
            return ""
        if self._read_wifi_mode(wifi.device_path) == NM_802_11_MODE_AP:
            return active
        connection_path = self._connection_path_for_active(active)
        hotspot_path = self._find_hotspot_connection_path()
        if connection_path and hotspot_path and connection_path == hotspot_path:
            return active
        return ""

    def _connection_path_for_active(self, active_path: str) -> str:
        if self._bus is None or not active_path or active_path == "/":
            return ""
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                active_path,
                NM_ACTIVE_CONNECTION_INTERFACE,
                None,
            )
            return str(_cached_property(proxy, "Connection", "/") or "/")
        except GLib.Error:
            return ""

    def _find_hotspot_connection_path(self) -> str:
        preferred = ""
        fallback = ""
        wifi = self._snapshot.wifi
        interface_name = wifi.interface if wifi is not None else ""
        for path in self._list_connection_paths():
            settings = self._get_connection_settings(path)
            if settings is None or not connection_is_hotspot(settings):
                continue
            connection = unpack_settings_map(settings.get("connection"))
            conn_id = str(connection.get("id") or "").strip()
            iface = str(connection.get("interface-name") or "").strip()
            if conn_id == SHELL_HOTSPOT_CONNECTION_ID:
                preferred = path
                break
            if interface_name and iface == interface_name and not fallback:
                fallback = path
            elif not fallback:
                fallback = path
        return preferred or fallback

    def _list_connection_paths(self) -> tuple[str, ...]:
        if self._bus is None:
            return ()
        try:
            settings_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                NM_SETTINGS_PATH,
                NM_SETTINGS_INTERFACE,
                None,
            )
            result = settings_proxy.call_sync(
                "ListConnections",
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            return tuple(str(path) for path in result.unpack()[0])
        except GLib.Error as exc:
            _logger.debug("ListConnections failed: %s", exc.message)
            return ()

    def _get_connection_settings(self, connection_path: str) -> dict | None:
        if self._bus is None or not connection_path or connection_path == "/":
            return None
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                connection_path,
                NM_SETTINGS_CONNECTION_INTERFACE,
                None,
            )
            result = proxy.call_sync(
                "GetSettings",
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            settings = result.unpack()[0]
            if isinstance(settings, dict):
                return settings
            return unpack_settings_map(settings)
        except GLib.Error:
            return None

    def _hotspot_password_configured(self, connection_path: str) -> bool:
        settings = self._get_connection_settings(connection_path)
        if settings is None:
            return False
        return password_configured_from_settings(settings)

    def _read_nm_hostname(self) -> str:
        if self._bus is None:
            return ""
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                NM_SETTINGS_PATH,
                NM_SETTINGS_INTERFACE,
                None,
            )
            return str(_cached_property(proxy, "Hostname", "") or "").strip()
        except GLib.Error:
            return ""

    def _read_wifi_capabilities(self, device_path: str) -> WifiHotspotCapabilities | None:
        if self._bus is None or not device_path:
            return None
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_WIFI_INTERFACE,
                None,
            )
            flags = int(_cached_property(proxy, "WirelessCapabilities", 0) or 0)
            return decode_wifi_capabilities(flags)
        except GLib.Error:
            return None

    def _read_wifi_mode(self, device_path: str) -> int:
        if self._bus is None or not device_path:
            return 0
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_WIFI_INTERFACE,
                None,
            )
            return int(_cached_property(proxy, "Mode", 0) or 0)
        except GLib.Error:
            return 0

    def _read_hotspot_clients(self, device_path: str) -> tuple[HotspotClientSnapshot, ...]:
        if self._bus is None or not device_path:
            return ()
        if self._read_wifi_mode(device_path) != NM_802_11_MODE_AP:
            return ()
        try:
            wireless_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_WIFI_INTERFACE,
                None,
            )
            result = wireless_proxy.call_sync(
                "GetAllAccessPoints",
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            paths = tuple(str(path) for path in result.unpack()[0])
            active_path = str(_cached_property(wireless_proxy, "ActiveAccessPoint", "/") or "/")
        except GLib.Error as exc:
            _logger.debug("Hotspot client query failed: %s", exc.message)
            return ()

        clients: list[HotspotClientSnapshot] = []
        seen: set[str] = set()
        for path in paths:
            if path == active_path:
                continue
            try:
                ap_proxy = Gio.DBusProxy.new_sync(
                    self._bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    NM_BUS_NAME,
                    path,
                    NM_ACCESS_POINT_INTERFACE,
                    None,
                )
            except GLib.Error:
                continue
            mac = str(_cached_property(ap_proxy, "HwAddress", "") or "").strip()
            if not mac or mac in seen:
                continue
            seen.add(mac)
            clients.append(HotspotClientSnapshot(mac_address=mac))
        clients.sort(key=lambda item: item.mac_address)
        return tuple(clients)

    def _read_hotspot_snapshot(
        self,
        wifi: NetworkInterfaceSnapshot | None,
        ethernet: NetworkInterfaceSnapshot | None,
        *,
        wireless_hardware_enabled: bool,
    ) -> HotspotSnapshot:
        capabilities = None
        wifi_mode = 0
        ssid = ""
        band = "auto"
        password_configured = False
        ipv4_shared = False
        forwarding_enabled = _ipv4_forwarding_enabled()
        connection_path = ""
        clients: tuple[HotspotClientSnapshot, ...] = ()
        if wifi is not None and wifi.device_path:
            capabilities = self._read_wifi_capabilities(wifi.device_path)
            wifi_mode = self._read_wifi_mode(wifi.device_path)
            connection_path = self._find_hotspot_connection_path()
            if connection_path:
                settings = self._get_connection_settings(connection_path)
                if settings is not None:
                    wireless = unpack_settings_map(settings.get("802-11-wireless"))
                    ssid = ssid_from_wireless_settings(wireless)
                    band = band_from_nm(wireless.get("band"))
                    password_configured = password_configured_from_settings(settings)
                    ipv4_shared = ipv4_shared_from_settings(settings)
            if wifi_mode == NM_802_11_MODE_AP:
                clients = self._read_hotspot_clients(wifi.device_path)
        if not ssid:
            ssid = default_hotspot_ssid(self._read_nm_hostname())
        pending = self._hotspot_pending_enable
        error_status = self._hotspot_last_error_status
        error_message = self._hotspot_last_error_message
        snapshot = assemble_hotspot_snapshot(
            wifi=wifi,
            ethernet=ethernet,
            capabilities=capabilities,
            wifi_mode=wifi_mode,
            ssid=ssid,
            band=band,
            password_configured=password_configured,
            ipv4_shared=ipv4_shared,
            forwarding_enabled=forwarding_enabled,
            clients=clients,
            connection_path=connection_path,
            pending_enable=pending,
            last_error_status=error_status,
            last_error_message=error_message,
            wireless_hardware_enabled=wireless_hardware_enabled,
        )
        if snapshot.active:
            self._hotspot_pending_enable = False
            self._cancel_hotspot_activate_retry()
            self._clear_hotspot_error()
            snapshot = assemble_hotspot_snapshot(
                wifi=wifi,
                ethernet=ethernet,
                capabilities=capabilities,
                wifi_mode=wifi_mode,
                ssid=ssid,
                band=band,
                password_configured=password_configured,
                ipv4_shared=ipv4_shared,
                forwarding_enabled=forwarding_enabled,
                clients=clients,
                connection_path=connection_path,
                pending_enable=False,
                last_error_status="",
                last_error_message="",
                wireless_hardware_enabled=wireless_hardware_enabled,
            )
        return snapshot

    def _store_hotspot_dbus_error(self, exc: GLib.Error) -> None:
        message = sanitize_hotspot_error_message(exc.message)
        status = classify_dbus_hotspot_error(exc.message)
        self._store_hotspot_error(message, status=status)
        _logger.warning("Hotspot NetworkManager error: %s", message)

    def _store_hotspot_error(self, message: str, *, status: str = "config_error") -> None:
        self._hotspot_last_error_status = (
            status if status in {"auth_error", "config_error"} else "config_error"
        )
        self._hotspot_last_error_message = message

    def _clear_hotspot_error(self) -> None:
        self._hotspot_last_error_status = ""
        self._hotspot_last_error_message = ""

    def _cancel_hotspot_activate_retry(self) -> None:
        if self._hotspot_activate_source_id:
            GLib.source_remove(self._hotspot_activate_source_id)
            self._hotspot_activate_source_id = 0
        self._hotspot_activate_delays = []

    def _schedule_hotspot_activate_retry(self, ssid: str, password: str, band: str) -> None:
        if self._hotspot_activate_source_id:
            GLib.source_remove(self._hotspot_activate_source_id)
            self._hotspot_activate_source_id = 0
        if not self._hotspot_pending_enable:
            return
        if not self._hotspot_activate_delays:
            self._hotspot_pending_enable = False
            self._store_hotspot_error("El adaptador Wi-Fi no quedó listo a tiempo")
            self._schedule_refresh()
            return
        delay = self._hotspot_activate_delays.pop(0)
        self._hotspot_activate_source_id = GLib.timeout_add(
            delay,
            self._hotspot_activate_retry_tick,
            ssid,
            password,
            band,
        )

    def _hotspot_activate_retry_tick(self, ssid: str, password: str, band: str) -> bool:
        self._hotspot_activate_source_id = 0
        if not self._hotspot_pending_enable:
            return False
        self._refresh_snapshot(emit=False, force_connectivity_check=False)
        if self._snapshot.hotspot.active:
            self._hotspot_pending_enable = False
            self._schedule_refresh()
            return False
        if not self._activate_hotspot(ssid=ssid, password=password, band=band):
            self._schedule_hotspot_activate_retry(ssid, password, band)
        self._schedule_refresh()
        return False

    def _toggle_ethernet_idle(self) -> bool:
        ethernet = self._snapshot.ethernet
        if ethernet is None or not ethernet.device_path:
            _logger.info("Ethernet toggle ignored: no managed ethernet device")
            return False
        if ethernet.connected:
            if self._disconnect_device(ethernet.device_path):
                self._cancel_connectivity_followup()
                self._schedule_refresh()
        else:
            if self._connect_device(ethernet.device_path):
                self._schedule_connectivity_followup(trigger="toggle-connect")
        return False

    def _connect_device(self, device_path: str) -> bool:
        if self._bus is None or self._nm_proxy is None:
            return False
        try:
            device_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_INTERFACE,
                None,
            )
            available = _cached_property(device_proxy, "AvailableConnections", ())
            if not available:
                _logger.warning(
                    "ActivateConnection skipped: no saved profiles for %s",
                    device_path,
                )
                return False
            connection_path = str(available[0])
            self._nm_proxy.call_sync(
                "ActivateConnection",
                GLib.Variant("(ooo)", (connection_path, device_path, "/")),
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            _logger.warning(
                "ActivateConnection failed for %s: %s",
                device_path,
                exc.message,
            )
            return False

    def _disconnect_device(self, device_path: str) -> bool:
        if self._bus is None:
            return False
        try:
            device_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                device_path,
                NM_DEVICE_INTERFACE,
                None,
            )
            device_proxy.call_sync(
                "Disconnect",
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            _logger.warning(
                "Disconnect failed for %s: %s",
                device_path,
                exc.message,
            )
            return False

    def _connect_network_manager(self) -> bool:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._nm_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                NM_BUS_NAME,
                NM_OBJECT_PATH,
                NM_INTERFACE,
                None,
            )
        except GLib.Error as exc:
            _logger.warning("NetworkManager unavailable: %s", exc)
            self._bus = None
            self._nm_proxy = None
            return False

        self._nm_handler_ids.extend(
            (
                self._nm_proxy.connect("notify::Devices", self._schedule_refresh),
                self._nm_proxy.connect(
                    "notify::Connectivity",
                    self._on_connectivity_property_changed,
                ),
                self._nm_proxy.connect(
                    "notify::WirelessEnabled",
                    self._schedule_refresh,
                ),
                self._nm_proxy.connect("g-signal", self._on_nm_signal),
            ),
        )
        return True

    def _on_connectivity_property_changed(self, *_args) -> None:
        self._schedule_refresh()

    def _on_nm_signal(
        self,
        _proxy: Gio.DBusProxy,
        _sender: str,
        signal_name: str,
        _parameters: GLib.Variant,
    ) -> None:
        if signal_name in {
            "DeviceAdded",
            "DeviceRemoved",
            "PropertiesChanged",
            "StateChanged",
        }:
            self._schedule_refresh()

    def _scan_devices(self) -> None:
        if self._bus is None or self._nm_proxy is None:
            return
        try:
            result = self._nm_proxy.call_sync(
                "GetDevices",
                None,
                Gio.DBusCallFlags.NONE,
                NETWORK_DBUS_TIMEOUT_MS,
                None,
            )
            device_paths = tuple(str(path) for path in result.unpack()[0])
        except GLib.Error as exc:
            _logger.warning("GetDevices failed: %s", exc)
            return

        current = set(device_paths)
        for path in list(self._device_watchers):
            if path not in current:
                self._device_watchers.pop(path).disconnect()

        for path in device_paths:
            if path in self._device_watchers:
                continue
            try:
                self._device_watchers[path] = _DeviceWatcher(
                    self._bus,
                    path,
                    self._schedule_refresh,
                )
            except GLib.Error as exc:
                _logger.debug("Skipping device watcher for %s: %s", path, exc)

    def _schedule_refresh(self, *_args) -> None:
        if self._refresh_source_id:
            return
        self._refresh_source_id = GLib.timeout_add(
            NETWORK_REFRESH_DEBOUNCE_MS,
            self._refresh_from_idle,
        )

    def _refresh_from_idle(self) -> bool:
        self._refresh_source_id = 0
        self._refresh_snapshot(emit=True, force_connectivity_check=False)
        return False

    def _cancel_refresh(self) -> None:
        if self._refresh_source_id:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = 0

    def _cancel_connectivity_followup(self) -> None:
        if self._connectivity_followup_source_id:
            GLib.source_remove(self._connectivity_followup_source_id)
            self._connectivity_followup_source_id = 0
        self._connectivity_followup_delays = []

    def _schedule_connectivity_followup(self, *, trigger: str = "") -> None:
        """Run targeted CheckConnectivity retries after connect/state transitions."""
        self._cancel_connectivity_followup()
        self._connectivity_followup_delays = list(NETWORK_CONNECTIVITY_FOLLOWUP_DELAYS_MS)
        _logger.debug("Scheduling connectivity followup (%s)", trigger)
        self._refresh_snapshot(emit=True, force_connectivity_check=True)
        if (
            self._snapshot.ethernet
            and self._snapshot.ethernet.connected
            and self._snapshot.connectivity.has_internet
        ):
            self._cancel_connectivity_followup()
            return
        self._queue_connectivity_followup_tick()

    def _queue_connectivity_followup_tick(self) -> None:
        if not self._connectivity_followup_delays:
            return
        delay = self._connectivity_followup_delays.pop(0)
        self._connectivity_followup_source_id = GLib.timeout_add(
            delay,
            self._connectivity_followup_tick,
        )

    def _connectivity_followup_tick(self) -> bool:
        self._connectivity_followup_source_id = 0
        self._refresh_snapshot(emit=True, force_connectivity_check=True)
        if (
            self._snapshot.ethernet
            and self._snapshot.ethernet.connected
            and self._snapshot.connectivity.has_internet
        ):
            self._cancel_connectivity_followup()
            return False
        if self._connectivity_followup_delays:
            self._queue_connectivity_followup_tick()
        return False

    def _cancel_wifi_scan_followup(self) -> None:
        if self._wifi_scan_followup_source_id:
            GLib.source_remove(self._wifi_scan_followup_source_id)
            self._wifi_scan_followup_source_id = 0
        self._wifi_scan_followup_delays = []
        self._wifi_scan_pending = False

    def _schedule_wifi_scan_when_ready(self) -> None:
        """Retry scan follow-ups until the Wi-Fi device leaves unavailable."""
        self._wifi_scan_pending = True
        if self._wifi_scan_followup_source_id or self._wifi_scan_followup_delays:
            return
        self._wifi_scan_followup_delays = list(NETWORK_WIFI_SCAN_FOLLOWUP_DELAYS_MS)
        self._queue_wifi_scan_followup_tick()

    def _schedule_wifi_scan_followup(self) -> None:
        self._wifi_scan_followup_delays = list(NETWORK_WIFI_SCAN_FOLLOWUP_DELAYS_MS)
        self._refresh_snapshot(emit=True, force_connectivity_check=False)
        if not self._wifi_scan_followup_source_id:
            self._queue_wifi_scan_followup_tick()

    def _queue_wifi_scan_followup_tick(self) -> None:
        if not self._wifi_scan_followup_delays:
            return
        delay = self._wifi_scan_followup_delays.pop(0)
        self._wifi_scan_followup_source_id = GLib.timeout_add(
            delay,
            self._wifi_scan_followup_tick,
        )

    def _wifi_scan_followup_tick(self) -> bool:
        self._wifi_scan_followup_source_id = 0
        if self._wifi_scan_pending and wifi_scan_allowed(self._snapshot):
            self._request_wifi_scan_idle()
        else:
            self._refresh_snapshot(emit=True, force_connectivity_check=False)
        if self._wifi_scan_followup_delays:
            self._queue_wifi_scan_followup_tick()
        elif self._wifi_scan_pending and not wifi_scan_allowed(self._snapshot):
            _logger.debug("Wi-Fi scan follow-up finished while interface still unavailable")
            self._wifi_scan_pending = False
        return False

    def _refresh_snapshot(
        self,
        *,
        emit: bool,
        force_connectivity_check: bool = False,
    ) -> None:
        if self._bus is None or self._nm_proxy is None:
            return

        previous = self._snapshot
        interfaces: list[NetworkInterfaceSnapshot] = []
        for path in sorted(self._device_watchers):
            snapshot = read_interface_snapshot(self._bus, path)
            if snapshot is not None:
                interfaces.append(snapshot)

        connectivity = read_manager_connectivity(
            self._nm_proxy,
            force_check=force_connectivity_check,
        )
        wireless_enabled, wireless_hardware_enabled = read_wireless_radio_state(
            self._nm_proxy,
        )
        wifi_access_points: tuple[WifiAccessPointSnapshot, ...] = ()
        wifi_iface = select_wifi_interface(tuple(interfaces))
        ethernet_iface = select_ethernet_interface(tuple(interfaces))
        wifi_mode = (
            self._read_wifi_mode(wifi_iface.device_path)
            if wifi_iface is not None and wifi_iface.device_path
            else 0
        )
        if (
            wireless_enabled
            and wifi_iface is not None
            and wifi_iface.device_path
            and wifi_iface.state not in ("unavailable", "unmanaged", "unknown")
            and wifi_mode != NM_802_11_MODE_AP
        ):
            wifi_access_points = read_wifi_access_points(
                self._bus,
                wifi_iface.device_path,
            )

        hotspot = self._read_hotspot_snapshot(
            wifi_iface,
            ethernet_iface,
            wireless_hardware_enabled=wireless_hardware_enabled,
        )
        next_snapshot = compose_network_snapshot(
            tuple(interfaces),
            connectivity=connectivity,
            wireless_enabled=wireless_enabled,
            wireless_hardware_enabled=wireless_hardware_enabled,
            wifi_access_points=wifi_access_points,
            wifi_connection_target=self._wifi_connection_target,
            wifi_connection_error=self._wifi_connection_error,
            hotspot=hotspot,
        )
        if wifi_iface is not None and wifi_iface.connected:
            if self._wifi_connection_target:
                self._cancel_wifi_connection_timeout()
                self._wifi_connection_target = ""
                self._wifi_connection_error = ""
                next_snapshot = compose_network_snapshot(
                    tuple(interfaces),
                    connectivity=connectivity,
                    wireless_enabled=wireless_enabled,
                    wireless_hardware_enabled=wireless_hardware_enabled,
                    wifi_access_points=wifi_access_points,
                    wifi_connection_target="",
                    wifi_connection_error="",
                    hotspot=hotspot,
                )
        elif wifi_iface is not None and wifi_iface.state == "failed" and self._wifi_connection_target:
            self._wifi_connection_error = (
                f"No se pudo conectar a {self._wifi_connection_target}. Comprueba la contraseña."
            )
            next_snapshot = compose_network_snapshot(
                tuple(interfaces),
                connectivity=connectivity,
                wireless_enabled=wireless_enabled,
                wireless_hardware_enabled=wireless_hardware_enabled,
                wifi_access_points=wifi_access_points,
                wifi_connection_target=self._wifi_connection_target,
                wifi_connection_error=self._wifi_connection_error,
                hotspot=hotspot,
            )
        if next_snapshot == self._snapshot:
            return
        self._snapshot = next_snapshot
        if emit:
            self._event_bus.emit(NETWORK_CHANGED, self._snapshot)
        if (
            not force_connectivity_check
            and should_schedule_connectivity_followup(previous, next_snapshot)
        ):
            self._schedule_connectivity_followup(trigger="state-transition")

    def _schedule_fallback_poll(self) -> None:
        if self._fallback_source_id:
            return
        self._fallback_source_id = GLib.timeout_add_seconds(
            NETWORK_FALLBACK_POLL_SEC,
            self._fallback_poll_tick,
        )

    def _fallback_poll_tick(self) -> bool:
        if self._connect_network_manager():
            if self._fallback_source_id:
                GLib.source_remove(self._fallback_source_id)
                self._fallback_source_id = 0
            self._scan_devices()
            self._refresh_snapshot(emit=True)
            return False
        return True
