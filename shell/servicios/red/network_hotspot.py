"""NetworkManager AP / shared-connection helpers used by NetworkService.

This module has no EventBus of its own. NetworkService remains the only
D-Bus owner and snapshot publisher.
"""

from __future__ import annotations

from typing import Any

from gi.repository import GLib

from ...models import (
    HotspotClientSnapshot,
    HotspotSnapshot,
    NetworkInterfaceSnapshot,
    WifiHotspotCapabilities,
)

SHELL_HOTSPOT_CONNECTION_ID = "Shell Hotspot"
HOTSPOT_PSK_MIN_LENGTH = 8
HOTSPOT_SSID_MAX_BYTES = 32

NM_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
NM_SETTINGS_INTERFACE = "org.freedesktop.NetworkManager.Settings"
NM_SETTINGS_CONNECTION_INTERFACE = "org.freedesktop.NetworkManager.Settings.Connection"

NM_WIFI_DEVICE_CAP_AP = 0x00000040
NM_WIFI_DEVICE_CAP_FREQ_VALID = 0x00000100
NM_WIFI_DEVICE_CAP_FREQ_2GHZ = 0x00000200
NM_WIFI_DEVICE_CAP_FREQ_5GHZ = 0x00000400

NM_802_11_MODE_AP = 3

HOTSPOT_BAND_AUTO = "auto"
HOTSPOT_BAND_24 = "2.4"
HOTSPOT_BAND_5 = "5"
HOTSPOT_BANDS = (HOTSPOT_BAND_AUTO, HOTSPOT_BAND_24, HOTSPOT_BAND_5)

NM_BAND_24 = "bg"
NM_BAND_5 = "a"

HOTSPOT_STATUS_WIFI_UNAVAILABLE = "wifi_unavailable"
HOTSPOT_STATUS_AP_UNSUPPORTED = "ap_unsupported"
HOTSPOT_STATUS_OFF = "off"
HOTSPOT_STATUS_STARTING = "starting"
HOTSPOT_STATUS_ACTIVE = "active"
HOTSPOT_STATUS_SHARING = "sharing"
HOTSPOT_STATUS_ACTIVE_NO_UPSTREAM = "active_no_upstream"
HOTSPOT_STATUS_ACTIVE_NO_FORWARDING = "active_no_forwarding"
HOTSPOT_STATUS_AUTH_ERROR = "auth_error"
HOTSPOT_STATUS_CONFIG_ERROR = "config_error"


def unpack_setting_value(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value


def unpack_settings_map(value: Any) -> dict[str, Any]:
    raw = unpack_setting_value(value)
    if not isinstance(raw, dict):
        return {}
    return {str(key): unpack_setting_value(item) for key, item in raw.items()}


def decode_wifi_capabilities(flags: int) -> WifiHotspotCapabilities:
    caps = int(flags)
    supports_ap = bool(caps & NM_WIFI_DEVICE_CAP_AP)
    if caps & NM_WIFI_DEVICE_CAP_FREQ_VALID:
        supports_2_4 = bool(caps & NM_WIFI_DEVICE_CAP_FREQ_2GHZ)
        supports_5 = bool(caps & NM_WIFI_DEVICE_CAP_FREQ_5GHZ)
    else:
        supports_2_4 = supports_ap
        supports_5 = False
    return WifiHotspotCapabilities(
        supports_ap=supports_ap,
        supports_2_4ghz=supports_2_4,
        supports_5ghz=supports_5,
    )


def nm_band_value(band: str) -> str | None:
    if band == HOTSPOT_BAND_AUTO:
        return NM_BAND_24
    if band == HOTSPOT_BAND_24:
        return NM_BAND_24
    if band == HOTSPOT_BAND_5:
        return NM_BAND_5
    return None


def band_from_nm(value: Any) -> str:
    raw = str(unpack_setting_value(value) or "").strip()
    if raw == NM_BAND_5:
        return HOTSPOT_BAND_5
    if raw == NM_BAND_24:
        return HOTSPOT_BAND_24
    return HOTSPOT_BAND_AUTO


def normalize_hotspot_ssid(ssid: str, *, fallback: str = "") -> str:
    candidate = (ssid or "").strip() or (fallback or "").strip()
    encoded = candidate.encode("utf-8")
    if len(encoded) <= HOTSPOT_SSID_MAX_BYTES:
        return candidate
    clipped = encoded[:HOTSPOT_SSID_MAX_BYTES]
    return clipped.decode("utf-8", errors="ignore").rstrip()


def default_hotspot_ssid(hostname: str) -> str:
    host = (hostname or "PC").split(".")[0].strip() or "PC"
    return normalize_hotspot_ssid(f"{host}-WiFi", fallback="PC-WiFi")


def decode_ssid_bytes(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace").strip()
    if isinstance(raw, (list, tuple)):
        return bytes(raw).decode("utf-8", errors="replace").strip()
    return str(raw).strip()


def ssid_from_wireless_settings(wireless: dict[str, Any]) -> str:
    return decode_ssid_bytes(unpack_setting_value(wireless.get("ssid")))


def password_configured_from_settings(settings: dict[str, Any]) -> bool:
    security = unpack_settings_map(settings.get("802-11-wireless-security"))
    key_mgmt = str(unpack_setting_value(security.get("key-mgmt") or "")).strip()
    return key_mgmt == "wpa-psk"


def ipv4_shared_from_settings(settings: dict[str, Any]) -> bool:
    ipv4 = unpack_settings_map(settings.get("ipv4"))
    method = str(unpack_setting_value(ipv4.get("method") or "")).strip()
    return method == "shared"


def connection_is_hotspot(settings: dict[str, Any]) -> bool:
    connection = unpack_settings_map(settings.get("connection"))
    conn_type = str(unpack_setting_value(connection.get("type") or "")).strip()
    if conn_type != "802-11-wireless":
        return False
    wireless = unpack_settings_map(settings.get("802-11-wireless"))
    mode = str(unpack_setting_value(wireless.get("mode") or "")).strip()
    return mode == "ap"


def classify_dbus_hotspot_error(message: str) -> str:
    text = (message or "").lower()
    auth_markers = (
        "notauthorized",
        "not authorized",
        "access denied",
        "permission denied",
        "polkit",
        "not authorized for this operation",
        "gd.bus.error.accessdenied",
    )
    if any(marker in text for marker in auth_markers):
        return HOTSPOT_STATUS_AUTH_ERROR
    return HOTSPOT_STATUS_CONFIG_ERROR


def sanitize_hotspot_error_message(message: str) -> str:
    """Drop likely secret material from NM/D-Bus error text before logging or UI."""
    text = (message or "").strip()
    lowered = text.lower()
    if "psk" in lowered or "password" in lowered or "secret" in lowered:
        return "NetworkManager rechazó la configuración del punto de acceso"
    return text


def hotspot_connection_settings_variant(
    ssid: str,
    password: str,
    band: str,
    *,
    interface_name: str = "",
    connection_id: str = SHELL_HOTSPOT_CONNECTION_ID,
    uuid: str = "",
) -> GLib.Variant:
    wireless: dict[str, GLib.Variant] = {
        "ssid": GLib.Variant("ay", ssid.encode("utf-8")),
        "mode": GLib.Variant("s", "ap"),
    }
    nm_band = nm_band_value(band)
    if nm_band is not None:
        wireless["band"] = GLib.Variant("s", nm_band)
    connection: dict[str, GLib.Variant] = {
        "type": GLib.Variant("s", "802-11-wireless"),
        "id": GLib.Variant("s", connection_id),
        "autoconnect": GLib.Variant("b", False),
    }
    if interface_name:
        connection["interface-name"] = GLib.Variant("s", interface_name)
    if uuid:
        connection["uuid"] = GLib.Variant("s", uuid)
    sections: list[tuple[str, dict[str, GLib.Variant]]] = [
        ("connection", connection),
        ("802-11-wireless", wireless),
        ("ipv4", {"method": GLib.Variant("s", "shared")}),
        ("ipv6", {"method": GLib.Variant("s", "ignore")}),
        (
            "802-11-wireless-security",
            {
                "key-mgmt": GLib.Variant("s", "wpa-psk"),
                "proto": GLib.Variant("as", ["rsn"]),
                "pairwise": GLib.Variant("as", ["ccmp"]),
                "group": GLib.Variant("as", ["ccmp"]),
            },
        ),
    ]
    if password:
        sections[-1][1]["psk"] = GLib.Variant("s", password)
    return GLib.Variant("a{sa{sv}}", sections)


def assemble_hotspot_snapshot(
    *,
    wifi: NetworkInterfaceSnapshot | None,
    ethernet: NetworkInterfaceSnapshot | None,
    capabilities: WifiHotspotCapabilities | None,
    wifi_mode: int,
    ssid: str,
    band: str,
    password_configured: bool,
    ipv4_shared: bool,
    clients: tuple[HotspotClientSnapshot, ...],
    connection_path: str,
    pending_enable: bool,
    last_error_status: str,
    last_error_message: str,
    wireless_hardware_enabled: bool,
    forwarding_enabled: bool = True,
) -> HotspotSnapshot:
    caps = capabilities or WifiHotspotCapabilities()
    available = wifi is not None and bool(wifi.device_path) and wireless_hardware_enabled
    wifi_device = wifi.interface if wifi is not None else ""
    mode_is_ap = int(wifi_mode) == NM_802_11_MODE_AP
    wifi_state = wifi.state if wifi is not None else ""
    active = bool(
        available
        and caps.supports_ap
        and mode_is_ap
        and wifi_state == "connected",
    )
    starting = bool(
        pending_enable
        or (
            available
            and caps.supports_ap
            and mode_is_ap
            and wifi_state in {"connecting", "disconnecting"}
        ),
    )
    ethernet_upstream = bool(ethernet is not None and ethernet.connected)
    sharing = active and ipv4_shared and ethernet_upstream and forwarding_enabled

    if last_error_status in {HOTSPOT_STATUS_AUTH_ERROR, HOTSPOT_STATUS_CONFIG_ERROR}:
        if not active and not starting:
            status = last_error_status
        elif starting:
            status = HOTSPOT_STATUS_STARTING
        elif sharing:
            status = HOTSPOT_STATUS_SHARING
        elif active and not forwarding_enabled:
            status = HOTSPOT_STATUS_ACTIVE_NO_FORWARDING
        elif active and not ethernet_upstream:
            status = HOTSPOT_STATUS_ACTIVE_NO_UPSTREAM
        elif active:
            status = HOTSPOT_STATUS_ACTIVE
        else:
            status = last_error_status
    elif not available:
        status = HOTSPOT_STATUS_WIFI_UNAVAILABLE
    elif not caps.supports_ap:
        status = HOTSPOT_STATUS_AP_UNSUPPORTED
    elif starting and not active:
        status = HOTSPOT_STATUS_STARTING
    elif sharing:
        status = HOTSPOT_STATUS_SHARING
    elif active and not forwarding_enabled:
        status = HOTSPOT_STATUS_ACTIVE_NO_FORWARDING
    elif active and not ethernet_upstream:
        status = HOTSPOT_STATUS_ACTIVE_NO_UPSTREAM
    elif active:
        status = HOTSPOT_STATUS_ACTIVE
    else:
        status = HOTSPOT_STATUS_OFF

    return HotspotSnapshot(
        status=status,
        available=available,
        supports_ap=caps.supports_ap,
        supports_2_4ghz=caps.supports_2_4ghz,
        supports_5ghz=caps.supports_5ghz,
        active=active,
        ssid=ssid,
        band=band if band in HOTSPOT_BANDS else HOTSPOT_BAND_AUTO,
        password_configured=password_configured,
        wifi_device=wifi_device,
        shared_connection=sharing,
        ipv4_shared=ipv4_shared,
        forwarding_enabled=forwarding_enabled,
        ethernet_upstream=ethernet_upstream,
        connected_clients=clients if active else (),
        error_message=last_error_message if status in {
            HOTSPOT_STATUS_AUTH_ERROR,
            HOTSPOT_STATUS_CONFIG_ERROR,
        } else "",
        connection_path=connection_path,
    )


def hotspot_status_label(snapshot: HotspotSnapshot) -> str:
    labels = {
        HOTSPOT_STATUS_WIFI_UNAVAILABLE: "Wi-Fi no disponible",
        HOTSPOT_STATUS_AP_UNSUPPORTED: "Este adaptador no admite modo punto de acceso",
        HOTSPOT_STATUS_OFF: "Punto de acceso apagado",
        HOTSPOT_STATUS_STARTING: "Iniciando punto de acceso…",
        HOTSPOT_STATUS_ACTIVE: "Punto de acceso activo",
        HOTSPOT_STATUS_SHARING: "Compartiendo Internet",
        HOTSPOT_STATUS_ACTIVE_NO_UPSTREAM: "Activo · sin Ethernet de origen",
        HOTSPOT_STATUS_ACTIVE_NO_FORWARDING: "Activo · forwarding desactivado",
        HOTSPOT_STATUS_AUTH_ERROR: "Error de autorización",
        HOTSPOT_STATUS_CONFIG_ERROR: "Error de configuración",
    }
    return labels.get(snapshot.status, snapshot.status)
