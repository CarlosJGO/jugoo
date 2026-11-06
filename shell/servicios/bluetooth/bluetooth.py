"""BlueZ-backed Bluetooth monitoring and control for Jugoo."""

from __future__ import annotations

import logging
import re
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from ...eventbus import EventBus
from ...models import (
    BluetoothDeviceSnapshot,
    BluetoothIncomingFile,
    BluetoothPairingChallenge,
    BluetoothSnapshot,
    BluetoothTransferSnapshot,
)
from .agent import BluetoothAgent, _PendingInvocation
from .obex import ObexTransferManager, device_supports_object_push

BLUETOOTH_CHANGED = "bluetooth_changed"
BLUETOOTH_CLICKED = "bluetooth_clicked"

BLUEZ_BUS_NAME = "org.bluez"
BLUEZ_ROOT_PATH = "/"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
BATTERY_INTERFACE = "org.bluez.Battery1"

DBUS_TIMEOUT_MS = 3_000
DEVICE_CALL_TIMEOUT_MS = 30_000
# Gamepads often need a long window to enter pairing mode after the scan starts.
DISCOVERY_TIMEOUT_MS = 60_000
RECEIVE_TIMEOUT_MS = 180_000
FALLBACK_POLL_SEC = 30
REFRESH_DEBOUNCE_MS = 150
PAIRABLE_WHILE_SCANNING_SEC = 90

_ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")

# Bluetooth CoD major class (bits 8-12).
_COD_MAJOR_PERIPHERAL = 5
_COD_MAJOR_AUDIO = 4
_COD_MAJOR_PHONE = 2
_COD_MAJOR_COMPUTER = 1
_COD_MAJOR_IMAGING = 6

# BLE Appearance values commonly used by HID controllers.
_APPEARANCE_HID = {
    0x03C0: ("input-gaming-symbolic", "Mando"),  # HID Generic
    0x03C1: ("input-keyboard-symbolic", "Teclado"),
    0x03C2: ("input-mouse-symbolic", "Ratón"),
    0x03C3: ("input-gaming-symbolic", "Joystick"),
    0x03C4: ("input-gaming-symbolic", "Mando"),
}

_ICON_MAP = {
    "audio-card": "audio-card-symbolic",
    "audio-headphones": "audio-headphones-symbolic",
    "audio-headset": "audio-headset-symbolic",
    "input-keyboard": "input-keyboard-symbolic",
    "input-mouse": "input-mouse-symbolic",
    "input-gaming": "input-gaming-symbolic",
    "phone": "phone-symbolic",
    "computer": "computer-symbolic",
    "printer": "printer-symbolic",
    "camera-photo": "camera-photo-symbolic",
    "camera-video": "camera-video-symbolic",
    "multimedia-player": "multimedia-player-symbolic",
}

_logger = logging.getLogger(__name__)


def bluetooth_icon_name(snapshot: BluetoothSnapshot) -> str:
    if not snapshot.available:
        return "bluetooth-hardware-disabled-symbolic"
    if not snapshot.powered:
        return "bluetooth-disabled-symbolic"
    if snapshot.discovering:
        return "bluetooth-acquiring-symbolic"
    if snapshot.connected_devices:
        return "bluetooth-active-symbolic"
    return "bluetooth-symbolic"


def bluetooth_visual_state(snapshot: BluetoothSnapshot) -> str:
    if not snapshot.available:
        return "unavailable"
    if not snapshot.powered:
        return "disabled"
    if snapshot.discovering:
        return "discovering"
    if snapshot.connected_devices:
        return "connected"
    return "disconnected"


def build_bluetooth_tooltip(snapshot: BluetoothSnapshot) -> str:
    if not snapshot.available:
        return "Bluetooth\nNo disponible"
    if not snapshot.powered:
        return "Bluetooth\nApagado"
    connected = snapshot.connected_devices
    if connected:
        names = ", ".join(device.name for device in connected[:3])
        return f"Bluetooth\nConectado: {names}"
    if snapshot.discovering:
        return "Bluetooth\nBuscando dispositivos…"
    return "Bluetooth\nEncendido"


def device_type_from_icon(icon: str) -> str:
    if not icon:
        return ""
    lowered = icon.casefold()
    if "headphone" in lowered or "headset" in lowered:
        return "Auriculares"
    if "audio" in lowered:
        return "Audio"
    if "mouse" in lowered:
        return "Ratón"
    if "keyboard" in lowered:
        return "Teclado"
    if "phone" in lowered:
        return "Teléfono"
    if "computer" in lowered:
        return "Ordenador"
    if "gaming" in lowered or "joystick" in lowered:
        return "Mando"
    if "printer" in lowered:
        return "Impresora"
    return ""


def device_class_major(class_code: int) -> int:
    return (int(class_code) >> 8) & 0x1F


def icon_and_type_from_class(class_code: int) -> tuple[str, str]:
    """Fallback icon/type from Bluetooth Class Of Device when Icon is empty."""
    major = device_class_major(class_code)
    if major == _COD_MAJOR_PERIPHERAL:
        # BlueZ client/agent.c style: bits 6-7 keyboard/pointing, bits 1-4 type.
        combo = (int(class_code) & 0xC0) >> 6
        peripheral = (int(class_code) & 0x1E) >> 1
        if combo == 0x01 or peripheral == 0x03:
            return "input-keyboard-symbolic", "Teclado"
        if combo == 0x02 or peripheral == 0x04:
            return "input-mouse-symbolic", "Ratón"
        if peripheral in {0x01, 0x02}:
            return "input-gaming-symbolic", "Mando"
        return "input-gaming-symbolic", "Mando"
    if major == _COD_MAJOR_AUDIO:
        return "audio-headphones-symbolic", "Auriculares"
    if major == _COD_MAJOR_PHONE:
        return "phone-symbolic", "Teléfono"
    if major == _COD_MAJOR_COMPUTER:
        return "computer-symbolic", "Ordenador"
    if major == _COD_MAJOR_IMAGING:
        return "camera-photo-symbolic", "Cámara"
    return "", ""


def icon_and_type_from_appearance(appearance: int) -> tuple[str, str]:
    return _APPEARANCE_HID.get(int(appearance), ("", ""))


def resolve_device_icon_and_type(
    *,
    icon: str,
    class_code: int | None = None,
    appearance: int | None = None,
) -> tuple[str, str]:
    mapped = _ICON_MAP.get(icon, icon)
    device_type = device_type_from_icon(mapped or icon)
    if mapped and device_type:
        return mapped, device_type
    if appearance:
        app_icon, app_type = icon_and_type_from_appearance(appearance)
        if app_icon:
            return app_icon, app_type
    if class_code:
        class_icon, class_type = icon_and_type_from_class(class_code)
        if class_icon:
            return class_icon, class_type
    return mapped or "bluetooth-symbolic", device_type


def normalize_address(value: str) -> str:
    return str(value or "").strip().upper()


def is_bluetooth_address(value: str) -> bool:
    return bool(_ADDRESS_RE.match(str(value or "").strip()))


def address_to_object_suffix(address: str) -> str:
    return "dev_" + normalize_address(address).replace(":", "_")


def _variant_value(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value


def _props_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): _variant_value(val) for key, val in raw.items()}


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compose_device_snapshot(
    path: str,
    props: dict[str, Any],
    *,
    battery_percent: int | None = None,
) -> BluetoothDeviceSnapshot:
    address = normalize_address(str(props.get("Address") or ""))
    alias = str(props.get("Alias") or "").strip()
    name = str(props.get("Name") or "").strip()
    icon = str(props.get("Icon") or "").strip()
    display = alias or name or address or path.rsplit("/", 1)[-1]
    paired = bool(props.get("Paired"))
    uuids = props.get("UUIDs")
    can_send = device_supports_object_push(uuids)
    if paired and not can_send and not uuids:
        # UUIDs often empty until services resolve; allow send attempt for paired.
        can_send = True
    resolved_icon, device_type = resolve_device_icon_and_type(
        icon=icon,
        class_code=_optional_int(props.get("Class")),
        appearance=_optional_int(props.get("Appearance")),
    )
    return BluetoothDeviceSnapshot(
        address=address,
        name=display,
        path=path,
        paired=paired,
        connected=bool(props.get("Connected")),
        trusted=bool(props.get("Trusted")),
        icon=resolved_icon,
        device_type=device_type,
        battery_percent=battery_percent,
        can_send_files=can_send,
    )


def sort_devices(
    devices: tuple[BluetoothDeviceSnapshot, ...],
) -> tuple[BluetoothDeviceSnapshot, ...]:
    return tuple(
        sorted(
            devices,
            key=lambda device: (
                0 if device.connected else 1,
                0 if device.paired else 1,
                device.name.lower(),
                device.address,
            ),
        )
    )


class BluetoothService:
    """Single source of truth for Bluetooth state via BlueZ D-Bus signals."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._snapshot = BluetoothSnapshot.empty(
            error_message="BlueZ no está instalado o el servicio no está disponible.",
        )
        self._started = False
        self._bus: Gio.DBusConnection | None = None
        self._adapter_path = ""
        self._objects: dict[str, dict[str, dict[str, Any]]] = {}
        self._signal_ids: list[int] = []
        self._refresh_source_id = 0
        self._fallback_source_id = 0
        self._discovery_timeout_id = 0
        self._receive_timeout_id = 0
        self._receiving = False
        self._agent = BluetoothAgent(
            lookup_device=self._lookup_device_label,
            on_challenge=self._on_agent_challenge,
            on_cancelled=self._on_agent_cancelled,
        )
        self._obex = ObexTransferManager(
            on_changed=self._on_obex_changed,
            on_incoming=self._on_obex_incoming,
        )
        self._display_hint: BluetoothPairingChallenge | None = None
        self._last_error = ""

    @property
    def snapshot(self) -> BluetoothSnapshot:
        return self._snapshot

    @property
    def available(self) -> bool:
        return bool(self._adapter_path)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if not self._connect_bluez():
            self._schedule_fallback_poll()
            self._refresh_snapshot(emit=True)
            return
        self._agent.register(self._bus)  # type: ignore[arg-type]
        self._obex.connect()
        self._refresh_snapshot(emit=True)

    def close(self) -> None:
        self._started = False
        self._cancel_refresh()
        self._cancel_discovery_timeout()
        self._cancel_receive_timeout()
        self._receiving = False
        if self._fallback_source_id:
            GLib.source_remove(self._fallback_source_id)
            self._fallback_source_id = 0
        self._obex.close()
        self._agent.unregister()
        if self._bus is not None:
            for signal_id in self._signal_ids:
                try:
                    self._bus.signal_unsubscribe(signal_id)
                except GLib.Error:
                    pass
        self._signal_ids.clear()
        self._bus = None
        self._adapter_path = ""
        self._objects.clear()

    def refresh(self) -> None:
        if self._bus is None:
            return
        self._load_managed_objects()
        self._refresh_snapshot(emit=True)

    def toggle_powered(self) -> None:
        GLib.idle_add(self._toggle_powered_idle)

    def set_powered(self, enabled: bool) -> None:
        GLib.idle_add(self._set_powered_idle, enabled)

    def start_discovery(self) -> None:
        GLib.idle_add(self._start_discovery_idle)

    def stop_discovery(self) -> None:
        GLib.idle_add(self._stop_discovery_idle)

    def connect_device(self, address: str) -> None:
        GLib.idle_add(self._connect_device_idle, normalize_address(address))

    def disconnect_device(self, address: str) -> None:
        GLib.idle_add(self._disconnect_device_idle, normalize_address(address))

    def pair_device(self, address: str) -> None:
        GLib.idle_add(self._pair_device_idle, normalize_address(address))

    def remove_device(self, address: str) -> None:
        GLib.idle_add(self._remove_device_idle, normalize_address(address))

    def accept_pairing(self, *, pin: str | None = None, passkey: int | None = None) -> None:
        GLib.idle_add(self._accept_pairing_idle, pin, passkey)

    def reject_pairing(self) -> None:
        GLib.idle_add(self._reject_pairing_idle)

    def set_receiving(self, enabled: bool) -> None:
        GLib.idle_add(self._set_receiving_idle, enabled)

    def send_file(self, address: str, file_path: str) -> None:
        GLib.idle_add(self._send_file_idle, normalize_address(address), file_path)

    def cancel_transfer(self) -> None:
        GLib.idle_add(self._cancel_transfer_idle)

    def accept_incoming_file(self) -> None:
        GLib.idle_add(self._accept_incoming_idle)

    def reject_incoming_file(self) -> None:
        GLib.idle_add(self._reject_incoming_idle)

    def _connect_bluez(self) -> bool:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as exc:
            _logger.warning("Bluetooth: system bus unavailable: %s", exc.message)
            self._last_error = "No se pudo conectar al bus del sistema (D-Bus)."
            return False

        if not self._load_managed_objects():
            self._last_error = "BlueZ no está instalado o el servicio no está disponible."
            return False
        if not self._adapter_path:
            self._last_error = "No hay adaptador Bluetooth disponible."
            return False

        self._subscribe_signals()
        self._last_error = ""
        return True

    def _load_managed_objects(self) -> bool:
        if self._bus is None:
            return False
        try:
            result = self._bus.call_sync(
                BLUEZ_BUS_NAME,
                BLUEZ_ROOT_PATH,
                OBJECT_MANAGER_INTERFACE,
                "GetManagedObjects",
                None,
                GLib.VariantType("(a{oa{sa{sv}}})"),
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            _logger.debug("GetManagedObjects failed: %s", exc.message)
            self._objects = {}
            self._adapter_path = ""
            return False

        managed = result.unpack()[0]
        objects: dict[str, dict[str, dict[str, Any]]] = {}
        for path, interfaces in managed.items():
            objects[str(path)] = {
                str(iface): _props_dict(props) for iface, props in interfaces.items()
            }
        self._objects = objects
        self._adapter_path = self._pick_adapter_path()
        return True

    def _pick_adapter_path(self) -> str:
        adapters = [
            path
            for path, ifaces in self._objects.items()
            if ADAPTER_INTERFACE in ifaces
        ]
        if not adapters:
            return ""
        adapters.sort()
        return adapters[0]

    def _subscribe_signals(self) -> None:
        assert self._bus is not None
        self._signal_ids.append(
            self._bus.signal_subscribe(
                BLUEZ_BUS_NAME,
                OBJECT_MANAGER_INTERFACE,
                "InterfacesAdded",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_interfaces_added,
                None,
            )
        )
        self._signal_ids.append(
            self._bus.signal_subscribe(
                BLUEZ_BUS_NAME,
                OBJECT_MANAGER_INTERFACE,
                "InterfacesRemoved",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_interfaces_removed,
                None,
            )
        )
        self._signal_ids.append(
            self._bus.signal_subscribe(
                BLUEZ_BUS_NAME,
                PROPERTIES_INTERFACE,
                "PropertiesChanged",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_properties_changed,
                None,
            )
        )

    def _on_interfaces_added(
        self,
        _connection: Gio.DBusConnection,
        _sender: str | None,
        _object_path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
        *_user_data: Any,
    ) -> None:
        path, interfaces = parameters.unpack()
        current = self._objects.setdefault(str(path), {})
        for iface, props in interfaces.items():
            current[str(iface)] = _props_dict(props)
        if ADAPTER_INTERFACE in interfaces and not self._adapter_path:
            self._adapter_path = str(path)
        self._schedule_refresh()

    def _on_interfaces_removed(
        self,
        _connection: Gio.DBusConnection,
        _sender: str | None,
        _object_path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
        *_user_data: Any,
    ) -> None:
        path, interfaces = parameters.unpack()
        current = self._objects.get(str(path))
        if current is None:
            return
        for iface in interfaces:
            current.pop(str(iface), None)
        if not current:
            self._objects.pop(str(path), None)
        if str(path) == self._adapter_path and ADAPTER_INTERFACE in interfaces:
            self._adapter_path = self._pick_adapter_path()
        self._schedule_refresh()

    def _on_properties_changed(
        self,
        _connection: Gio.DBusConnection,
        _sender: str | None,
        object_path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
        *_user_data: Any,
    ) -> None:
        iface, changed, _invalidated = parameters.unpack()
        if iface not in {ADAPTER_INTERFACE, DEVICE_INTERFACE, BATTERY_INTERFACE}:
            return
        current = self._objects.setdefault(str(object_path), {})
        props = current.setdefault(str(iface), {})
        props.update(_props_dict(changed))
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_source_id:
            return
        self._refresh_source_id = GLib.timeout_add(REFRESH_DEBOUNCE_MS, self._refresh_debounced)

    def _refresh_debounced(self) -> bool:
        self._refresh_source_id = 0
        self._refresh_snapshot(emit=True)
        return False

    def _cancel_refresh(self) -> None:
        if self._refresh_source_id:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = 0

    def _schedule_fallback_poll(self) -> None:
        if self._fallback_source_id:
            return
        self._fallback_source_id = GLib.timeout_add_seconds(
            FALLBACK_POLL_SEC,
            self._fallback_poll_tick,
        )

    def _fallback_poll_tick(self) -> bool:
        if not self._started:
            self._fallback_source_id = 0
            return False
        if self._bus is None and self._connect_bluez():
            self._agent.register(self._bus)  # type: ignore[arg-type]
            self._obex.connect()
            self._refresh_snapshot(emit=True)
            self._fallback_source_id = 0
            return False
        if self._bus is not None and not self._adapter_path:
            self._load_managed_objects()
            if self._adapter_path and not self._obex.available:
                self._obex.connect()
            self._refresh_snapshot(emit=True)
            if self._adapter_path:
                self._fallback_source_id = 0
                return False
        return True

    def _refresh_snapshot(self, *, emit: bool) -> None:
        previous = self._snapshot
        transfer = self._compose_transfer_snapshot()
        incoming = self._compose_incoming_file()
        if not self._adapter_path:
            message = self._last_error or (
                "BlueZ no está instalado o el servicio no está disponible."
            )
            self._snapshot = BluetoothSnapshot.empty(error_message=message)
            # Keep OBEX-only state visible if somehow present.
            if transfer is not None or incoming is not None:
                self._snapshot = BluetoothSnapshot(
                    available=False,
                    powered=False,
                    discovering=False,
                    error_message=message,
                    obex_available=self._obex.available,
                    incoming_file=incoming,
                    transfer=transfer,
                )
        else:
            adapter = self._objects.get(self._adapter_path, {}).get(ADAPTER_INTERFACE, {})
            devices: list[BluetoothDeviceSnapshot] = []
            for path, ifaces in self._objects.items():
                device_props = ifaces.get(DEVICE_INTERFACE)
                if device_props is None:
                    continue
                battery = ifaces.get(BATTERY_INTERFACE, {})
                percent = battery.get("Percentage")
                battery_percent = int(percent) if percent is not None else None
                devices.append(
                    compose_device_snapshot(
                        path,
                        device_props,
                        battery_percent=battery_percent,
                    )
                )
            pairing = self._current_pairing_challenge()
            self._snapshot = BluetoothSnapshot(
                available=True,
                powered=bool(adapter.get("Powered")),
                discovering=bool(adapter.get("Discovering")),
                adapter_path=self._adapter_path,
                adapter_address=normalize_address(str(adapter.get("Address") or "")),
                adapter_name=str(adapter.get("Alias") or adapter.get("Name") or ""),
                devices=sort_devices(tuple(devices)),
                error_message=self._last_error,
                pairing=pairing,
                receiving=self._receiving,
                discoverable=bool(adapter.get("Discoverable")),
                obex_available=self._obex.available,
                incoming_file=incoming,
                transfer=transfer,
            )
        if emit and self._snapshot != previous:
            self._event_bus.emit(BLUETOOTH_CHANGED, self._snapshot)

    def _compose_transfer_snapshot(self) -> BluetoothTransferSnapshot | None:
        if not self._obex.transfer_status and not self._obex.transfer_error:
            return None
        return BluetoothTransferSnapshot(
            direction=self._obex.transfer_direction,
            name=self._obex.transfer_name,
            status=self._obex.transfer_status,
            size=self._obex.transfer_size,
            transferred=self._obex.transfer_transferred,
            device_address=self._obex.transfer_device,
            error_message=self._obex.transfer_error,
        )

    def _compose_incoming_file(self) -> BluetoothIncomingFile | None:
        pending = self._obex.incoming
        if pending is None:
            return None
        return BluetoothIncomingFile(
            name=pending.name,
            size=pending.size,
            suggested_path=pending.suggested_path,
        )

    def _on_obex_changed(self) -> None:
        GLib.idle_add(self._obex_refresh_idle)

    def _on_obex_incoming(self) -> None:
        GLib.idle_add(self._obex_refresh_idle)

    def _obex_refresh_idle(self) -> bool:
        self._refresh_snapshot(emit=True)
        return False

    def _current_pairing_challenge(self) -> BluetoothPairingChallenge | None:
        pending = self._agent.pending
        if pending is not None and pending.kind not in {"display_pin", "display_passkey"}:
            return BluetoothPairingChallenge(
                kind=pending.kind,
                device_address=pending.device_address,
                device_name=pending.device_name,
                passkey=pending.passkey,
                hint=pending.hint,
            )
        return self._display_hint

    def _lookup_device_label(self, device_path: str) -> tuple[str, str]:
        props = self._objects.get(device_path, {}).get(DEVICE_INTERFACE, {})
        address = normalize_address(str(props.get("Address") or ""))
        name = str(props.get("Alias") or props.get("Name") or address or device_path)
        return address, name

    def _on_agent_challenge(self, pending: _PendingInvocation) -> None:
        challenge = BluetoothPairingChallenge(
            kind=pending.kind,
            device_address=pending.device_address,
            device_name=pending.device_name,
            passkey=pending.passkey,
            hint=pending.hint,
        )
        # Display-only / auto-accepted Just Works: keep an ephemeral hint in UI.
        if pending.kind in {"display_pin", "display_passkey", "authorization"}:
            self._display_hint = challenge
            if pending.kind == "authorization":
                # Clear the toast after a few seconds; pairing continues in BlueZ.
                GLib.timeout_add_seconds(8, self._clear_display_hint)
        else:
            self._display_hint = None
        self._refresh_snapshot(emit=True)

    def _clear_display_hint(self) -> bool:
        if self._display_hint is not None and self._display_hint.kind == "authorization":
            self._display_hint = None
            self._refresh_snapshot(emit=True)
        return False

    def _on_agent_cancelled(self) -> None:
        self._display_hint = None
        self._refresh_snapshot(emit=True)

    def _device_path_for_address(self, address: str) -> str | None:
        target = normalize_address(address)
        if not target:
            return None
        for path, ifaces in self._objects.items():
            props = ifaces.get(DEVICE_INTERFACE)
            if props is None:
                continue
            if normalize_address(str(props.get("Address") or "")) == target:
                return path
        if self._adapter_path:
            candidate = f"{self._adapter_path}/{address_to_object_suffix(target)}"
            if candidate in self._objects:
                return candidate
        return None

    def _set_adapter_property(self, name: str, value: GLib.Variant) -> bool:
        if self._bus is None or not self._adapter_path:
            return False
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                self._adapter_path,
                PROPERTIES_INTERFACE,
                "Set",
                GLib.Variant("(ssv)", (ADAPTER_INTERFACE, name, value)),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
            adapter = self._objects.setdefault(self._adapter_path, {}).setdefault(
                ADAPTER_INTERFACE,
                {},
            )
            adapter[name] = _variant_value(value)
            return True
        except GLib.Error as exc:
            self._last_error = exc.message
            _logger.debug("Set Adapter1.%s failed: %s", name, exc.message)
            return False

    def _set_device_property(self, path: str, name: str, value: GLib.Variant) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                path,
                PROPERTIES_INTERFACE,
                "Set",
                GLib.Variant("(ssv)", (DEVICE_INTERFACE, name, value)),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
            props = self._objects.setdefault(path, {}).setdefault(DEVICE_INTERFACE, {})
            props[name] = _variant_value(value)
            return True
        except GLib.Error as exc:
            _logger.debug("Set Device1.%s failed: %s", name, exc.message)
            return False

    def _trust_device(self, path: str) -> None:
        """Mark device Trusted so HID/A2DP reconnect and AuthorizeService stay quiet."""
        self._set_device_property(path, "Trusted", GLib.Variant("b", True))

    def _prepare_adapter_for_pairing(self) -> None:
        """Make the adapter accept new bonds (needed for many gamepads)."""
        if not self._adapter_path:
            return
        self._set_adapter_property("Pairable", GLib.Variant("b", True))
        self._set_adapter_property(
            "PairableTimeout",
            GLib.Variant("u", PAIRABLE_WHILE_SCANNING_SEC),
        )

    def _apply_discovery_filter(self) -> None:
        """Discover classic + LE so DualSense / Xbox / Switch / etc. appear."""
        if self._bus is None or not self._adapter_path:
            return
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                self._adapter_path,
                ADAPTER_INTERFACE,
                "SetDiscoveryFilter",
                GLib.Variant(
                    "(a{sv})",
                    (
                        {
                            "Transport": GLib.Variant("s", "auto"),
                            "DuplicateData": GLib.Variant("b", False),
                        },
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            # Older bluez without filters still discovers; non-fatal.
            _logger.debug("SetDiscoveryFilter failed: %s", exc.message)

    def _ensure_discovery_stopped(self) -> None:
        """BlueZ often refuses Connect/Pair while the adapter is still scanning."""
        if not self._snapshot.discovering and not (
            self._objects.get(self._adapter_path, {})
            .get(ADAPTER_INTERFACE, {})
            .get("Discovering")
        ):
            self._cancel_discovery_timeout()
            return
        self._stop_discovery_idle()

    def _call_device(self, path: str, method: str) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                path,
                DEVICE_INTERFACE,
                method,
                None,
                None,
                Gio.DBusCallFlags.NONE,
                DEVICE_CALL_TIMEOUT_MS,
                None,
            )
            self._last_error = ""
            return True
        except GLib.Error as exc:
            message = exc.message or str(exc)
            # Already paired/connected are not real failures for our UI.
            if "AlreadyExists" in message or "Already Connected" in message:
                self._last_error = ""
                return True
            self._last_error = message
            _logger.debug("Device1.%s(%s) failed: %s", method, path, message)
            self._refresh_snapshot(emit=True)
            return False

    def _toggle_powered_idle(self) -> bool:
        self._set_powered_idle(not self._snapshot.powered)
        return False

    def _set_powered_idle(self, enabled: bool) -> bool:
        if not self._adapter_path:
            return False
        if self._set_adapter_property("Powered", GLib.Variant("b", enabled)):
            adapter = self._objects.setdefault(self._adapter_path, {}).setdefault(
                ADAPTER_INTERFACE,
                {},
            )
            adapter["Powered"] = enabled
            if not enabled:
                self._cancel_discovery_timeout()
                if self._receiving:
                    self._set_receiving_idle(False)
            self._refresh_snapshot(emit=True)
        else:
            self._refresh_snapshot(emit=True)
        return False

    def _start_discovery_idle(self) -> bool:
        if self._bus is None or not self._adapter_path:
            return False
        if not self._snapshot.powered:
            self._set_powered_idle(True)
        # Reclaim default agent so pairing prompts (or Just Works) hit Jugoo.
        if self._bus is not None:
            self._agent.register(self._bus)
        self._prepare_adapter_for_pairing()
        self._apply_discovery_filter()
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                self._adapter_path,
                ADAPTER_INTERFACE,
                "StartDiscovery",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
            self._last_error = ""
            adapter = self._objects.setdefault(self._adapter_path, {}).setdefault(
                ADAPTER_INTERFACE,
                {},
            )
            adapter["Discovering"] = True
            self._arm_discovery_timeout()
            self._refresh_snapshot(emit=True)
        except GLib.Error as exc:
            message = exc.message or str(exc)
            if "InProgress" in message:
                self._last_error = ""
                adapter = self._objects.setdefault(self._adapter_path, {}).setdefault(
                    ADAPTER_INTERFACE,
                    {},
                )
                adapter["Discovering"] = True
                self._arm_discovery_timeout()
            else:
                self._last_error = message
                _logger.debug("StartDiscovery failed: %s", message)
            self._refresh_snapshot(emit=True)
        return False

    def _stop_discovery_idle(self) -> bool:
        self._cancel_discovery_timeout()
        if self._bus is None or not self._adapter_path:
            return False
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                self._adapter_path,
                ADAPTER_INTERFACE,
                "StopDiscovery",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            message = exc.message or str(exc)
            if "NotReady" not in message and "Failed" not in message:
                _logger.debug("StopDiscovery failed: %s", message)
        adapter = self._objects.setdefault(self._adapter_path, {}).setdefault(
            ADAPTER_INTERFACE,
            {},
        )
        adapter["Discovering"] = False
        # Keep Pairable for a bit so a follow-up Emparejar still works; receiving
        # mode owns Discoverable separately.
        if not self._receiving:
            self._set_adapter_property(
                "PairableTimeout",
                GLib.Variant("u", PAIRABLE_WHILE_SCANNING_SEC),
            )
        self._refresh_snapshot(emit=True)
        return False

    def _arm_discovery_timeout(self) -> None:
        self._cancel_discovery_timeout()
        self._discovery_timeout_id = GLib.timeout_add(
            DISCOVERY_TIMEOUT_MS,
            self._discovery_timeout_tick,
        )

    def _cancel_discovery_timeout(self) -> None:
        if self._discovery_timeout_id:
            GLib.source_remove(self._discovery_timeout_id)
            self._discovery_timeout_id = 0

    def _discovery_timeout_tick(self) -> bool:
        self._discovery_timeout_id = 0
        self._stop_discovery_idle()
        return False

    def _connect_device_idle(self, address: str) -> bool:
        path = self._device_path_for_address(address)
        if path is None:
            self._last_error = f"Dispositivo no encontrado: {address}"
            self._refresh_snapshot(emit=True)
            return False
        self._ensure_discovery_stopped()
        if not self._snapshot.powered:
            self._set_powered_idle(True)
        self._trust_device(path)
        if self._call_device(path, "Connect"):
            props = self._objects.setdefault(path, {}).setdefault(DEVICE_INTERFACE, {})
            props["Connected"] = True
        self._schedule_refresh()
        return False

    def _disconnect_device_idle(self, address: str) -> bool:
        path = self._device_path_for_address(address)
        if path is None:
            self._last_error = f"Dispositivo no encontrado: {address}"
            self._refresh_snapshot(emit=True)
            return False
        if self._call_device(path, "Disconnect"):
            props = self._objects.setdefault(path, {}).setdefault(DEVICE_INTERFACE, {})
            props["Connected"] = False
        self._schedule_refresh()
        return False

    def _pair_device_idle(self, address: str) -> bool:
        path = self._device_path_for_address(address)
        if path is None:
            self._last_error = f"Dispositivo no encontrado: {address}"
            self._refresh_snapshot(emit=True)
            return False
        self._ensure_discovery_stopped()
        if not self._snapshot.powered:
            self._set_powered_idle(True)
        if self._bus is not None:
            self._agent.register(self._bus)
        self._prepare_adapter_for_pairing()
        props = self._objects.get(path, {}).get(DEVICE_INTERFACE, {})
        already_paired = bool(props.get("Paired"))
        if not already_paired:
            if not self._call_device(path, "Pair"):
                return False
            props = self._objects.setdefault(path, {}).setdefault(DEVICE_INTERFACE, {})
            props["Paired"] = True
        self._trust_device(path)
        # Mandos / auriculares: conectar perfiles en el mismo gesto de emparejar.
        if self._call_device(path, "Connect"):
            props = self._objects.setdefault(path, {}).setdefault(DEVICE_INTERFACE, {})
            props["Connected"] = True
            self._last_error = ""
        self._schedule_refresh()
        return False

    def _remove_device_idle(self, address: str) -> bool:
        if self._bus is None or not self._adapter_path:
            return False
        path = self._device_path_for_address(address)
        if path is None:
            self._last_error = f"Dispositivo no encontrado: {address}"
            self._refresh_snapshot(emit=True)
            return False
        try:
            self._bus.call_sync(
                BLUEZ_BUS_NAME,
                self._adapter_path,
                ADAPTER_INTERFACE,
                "RemoveDevice",
                GLib.Variant("(o)", (path,)),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
            self._last_error = ""
            self._objects.pop(path, None)
            self._refresh_snapshot(emit=True)
        except GLib.Error as exc:
            self._last_error = exc.message
            _logger.debug("RemoveDevice failed: %s", exc.message)
            self._refresh_snapshot(emit=True)
        return False

    def _accept_pairing_idle(self, pin: str | None, passkey: int | None) -> bool:
        if self._agent.accept_pending(pin=pin, passkey=passkey):
            self._display_hint = None
            self._refresh_snapshot(emit=True)
        return False

    def _reject_pairing_idle(self) -> bool:
        self._agent.reject_pending()
        self._display_hint = None
        self._refresh_snapshot(emit=True)
        return False

    def _set_receiving_idle(self, enabled: bool) -> bool:
        if not self._adapter_path:
            return False
        if enabled and not self._snapshot.powered:
            self._set_powered_idle(True)
        if enabled and not self._obex.available:
            self._obex.connect()
        ok_disc = self._set_adapter_property("Discoverable", GLib.Variant("b", enabled))
        ok_pair = self._set_adapter_property("Pairable", GLib.Variant("b", enabled))
        if enabled:
            self._set_adapter_property(
                "DiscoverableTimeout",
                GLib.Variant("u", RECEIVE_TIMEOUT_MS // 1000),
            )
        adapter = self._objects.setdefault(self._adapter_path, {}).setdefault(
            ADAPTER_INTERFACE,
            {},
        )
        if ok_disc:
            adapter["Discoverable"] = enabled
        if ok_pair:
            adapter["Pairable"] = enabled
        self._receiving = bool(enabled and (ok_disc or ok_pair or self._obex.available))
        if self._receiving:
            self._arm_receive_timeout()
            self._last_error = ""
            if not self._obex.available:
                self._last_error = (
                    "Visible para recibir, pero OBEX no está disponible "
                    "(instala bluez-obex y activa obexd)."
                )
        else:
            self._cancel_receive_timeout()
            self._obex.reject_incoming()
        self._refresh_snapshot(emit=True)
        return False

    def _arm_receive_timeout(self) -> None:
        self._cancel_receive_timeout()
        self._receive_timeout_id = GLib.timeout_add(
            RECEIVE_TIMEOUT_MS,
            self._receive_timeout_tick,
        )

    def _cancel_receive_timeout(self) -> None:
        if self._receive_timeout_id:
            GLib.source_remove(self._receive_timeout_id)
            self._receive_timeout_id = 0

    def _receive_timeout_tick(self) -> bool:
        self._receive_timeout_id = 0
        self._set_receiving_idle(False)
        return False

    def _send_file_idle(self, address: str, file_path: str) -> bool:
        if not self._obex.available:
            self._obex.connect()
        if not address:
            self._last_error = "Dirección Bluetooth no válida"
            self._refresh_snapshot(emit=True)
            return False
        self._last_error = ""
        self._obex.send_file(address, file_path)
        self._refresh_snapshot(emit=True)
        return False

    def _cancel_transfer_idle(self) -> bool:
        self._obex.cancel_transfer()
        self._refresh_snapshot(emit=True)
        return False

    def _accept_incoming_idle(self) -> bool:
        self._obex.accept_incoming()
        self._refresh_snapshot(emit=True)
        return False

    def _reject_incoming_idle(self) -> bool:
        self._obex.reject_incoming()
        self._refresh_snapshot(emit=True)
        return False
