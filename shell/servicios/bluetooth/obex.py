"""BlueZ OBEX Object Push (send/receive) over the session bus."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

_logger = logging.getLogger(__name__)

OBEX_BUS_NAME = "org.bluez.obex"
OBEX_ROOT_PATH = "/org/bluez/obex"
OBEX_CLIENT_INTERFACE = "org.bluez.obex.Client1"
OBEX_OBJECT_PUSH_INTERFACE = "org.bluez.obex.ObjectPush1"
OBEX_TRANSFER_INTERFACE = "org.bluez.obex.Transfer1"
OBEX_AGENT_MANAGER_INTERFACE = "org.bluez.obex.AgentManager1"
OBEX_AGENT_INTERFACE = "org.bluez.obex.Agent1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

OBEX_AGENT_PATH = "/com/jugoo/shell/bluetooth/obex_agent"
DBUS_TIMEOUT_MS = 5_000
SESSION_TIMEOUT_MS = 60_000
TRANSFER_TIMEOUT_MS = 120_000

OPP_UUID = "00001105-0000-1000-8000-00805f9b34fb"

_SAFE_NAME_RE = re.compile(r"[^\w.\- ()\[\]]+", re.UNICODE)

_AGENT_XML = """
<node>
  <interface name="org.bluez.obex.Agent1">
    <method name="Release"/>
    <method name="AuthorizePush">
      <arg direction="in" type="o" name="transfer"/>
      <arg direction="out" type="s" name="path"/>
    </method>
    <method name="Cancel"/>
  </interface>
</node>
"""


@dataclass
class _IncomingPush:
    transfer_path: str
    name: str
    size: int | None
    suggested_path: str
    invocation: Gio.DBusMethodInvocation


def default_download_dir() -> Path:
    xdg = (os.environ.get("XDG_DOWNLOAD_DIR") or "").strip()
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / "Downloads"


def sanitize_filename(name: str) -> str:
    base = Path(str(name or "archivo")).name.strip() or "archivo"
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "archivo"
    return cleaned[:180]


def device_supports_object_push(uuids: Any) -> bool:
    if not uuids:
        return False
    needle = OPP_UUID.casefold()
    for item in uuids:
        if needle in str(item).casefold():
            return True
    return False


def _variant_value(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value


def _props_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): _variant_value(val) for key, val in raw.items()}


class ObexTransferManager:
    """Session-bus OBEX client + agent for Object Push send/receive."""

    def __init__(
        self,
        *,
        on_changed: Callable[[], None],
        on_incoming: Callable[[], None],
    ) -> None:
        self._on_changed = on_changed
        self._on_incoming = on_incoming
        self._bus: Gio.DBusConnection | None = None
        self._available = False
        self._agent_id = 0
        self._incoming: _IncomingPush | None = None
        self._session_path = ""
        self._transfer_path = ""
        self._transfer_direction = ""
        self._transfer_name = ""
        self._transfer_status = ""
        self._transfer_size: int | None = None
        self._transfer_done = 0
        self._transfer_error = ""
        self._transfer_device = ""
        self._signal_ids: list[int] = []
        self._node_info = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def incoming(self) -> _IncomingPush | None:
        return self._incoming

    @property
    def transfer_direction(self) -> str:
        return self._transfer_direction

    @property
    def transfer_name(self) -> str:
        return self._transfer_name

    @property
    def transfer_status(self) -> str:
        return self._transfer_status

    @property
    def transfer_size(self) -> int | None:
        return self._transfer_size

    @property
    def transfer_transferred(self) -> int:
        return self._transfer_done

    @property
    def transfer_error(self) -> str:
        return self._transfer_error

    @property
    def transfer_device(self) -> str:
        return self._transfer_device

    def connect(self) -> bool:
        self.close()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            _logger.warning("OBEX session bus unavailable: %s", exc.message)
            return False
        try:
            self._bus.call_sync(
                OBEX_BUS_NAME,
                OBEX_ROOT_PATH,
                "org.freedesktop.DBus.Introspectable",
                "Introspect",
                None,
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            _logger.debug("org.bluez.obex unavailable: %s", exc.message)
            self._bus = None
            return False
        self._available = True
        self._subscribe_transfer_signals()
        return self._register_agent()

    def close(self) -> None:
        self.reject_incoming()
        self._cleanup_session()
        if self._bus is not None:
            for signal_id in self._signal_ids:
                try:
                    self._bus.signal_unsubscribe(signal_id)
                except GLib.Error:
                    pass
            self._signal_ids.clear()
            self._unregister_agent()
        self._bus = None
        self._available = False
        self._clear_transfer_state()

    def send_file(self, address: str, file_path: str) -> bool:
        if self._bus is None or not self._available:
            self._transfer_error = "OBEX no está disponible (bluez-obex / obexd)."
            self._on_changed()
            return False
        path = Path(file_path).expanduser()
        if not path.is_file():
            self._transfer_error = f"Archivo no encontrado: {path}"
            self._on_changed()
            return False

        self._cleanup_session()
        self._transfer_direction = "send"
        self._transfer_name = path.name
        self._transfer_device = address
        self._transfer_status = "queued"
        self._transfer_error = ""
        self._transfer_size = path.stat().st_size
        self._transfer_done = 0
        self._on_changed()

        try:
            result = self._bus.call_sync(
                OBEX_BUS_NAME,
                OBEX_ROOT_PATH,
                OBEX_CLIENT_INTERFACE,
                "CreateSession",
                GLib.Variant(
                    "(sa{sv})",
                    (
                        address,
                        {"Target": GLib.Variant("s", "opp")},
                    ),
                ),
                GLib.VariantType("(o)"),
                Gio.DBusCallFlags.NONE,
                SESSION_TIMEOUT_MS,
                None,
            )
            session = str(result.unpack()[0])
            self._session_path = session
            push = self._bus.call_sync(
                OBEX_BUS_NAME,
                session,
                OBEX_OBJECT_PUSH_INTERFACE,
                "SendFile",
                GLib.Variant("(s)", (str(path.resolve()),)),
                GLib.VariantType("(oa{sv})"),
                Gio.DBusCallFlags.NONE,
                TRANSFER_TIMEOUT_MS,
                None,
            )
            transfer_path, props = push.unpack()
            self._apply_transfer_props(str(transfer_path), _props_dict(props))
            self._on_changed()
            return True
        except GLib.Error as exc:
            self._transfer_status = "error"
            self._transfer_error = exc.message
            _logger.debug("OBEX SendFile failed: %s", exc.message)
            self._cleanup_session()
            self._on_changed()
            return False

    def cancel_transfer(self) -> None:
        if self._bus is None or not self._transfer_path:
            return
        try:
            self._bus.call_sync(
                OBEX_BUS_NAME,
                self._transfer_path,
                OBEX_TRANSFER_INTERFACE,
                "Cancel",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            _logger.debug("OBEX Cancel failed: %s", exc.message)
        self._cleanup_session()
        if self._transfer_status not in {"complete", "error"}:
            self._transfer_status = "error"
            self._transfer_error = self._transfer_error or "Transferencia cancelada"
        self._on_changed()

    def accept_incoming(self, *, save_as: str | None = None) -> bool:
        pending = self._incoming
        if pending is None:
            return False
        self._incoming = None
        target = save_as or pending.suggested_path
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            try:
                pending.invocation.return_dbus_error(
                    "org.bluez.obex.Error.Rejected",
                    str(exc),
                )
            except GLib.Error:
                pass
            self._on_changed()
            return False
        try:
            pending.invocation.return_value(GLib.Variant("(s)", (target,)))
        except GLib.Error as exc:
            _logger.debug("AuthorizePush accept failed: %s", exc.message)
            self._on_changed()
            return False
        self._transfer_direction = "receive"
        self._transfer_name = pending.name
        self._transfer_size = pending.size
        self._transfer_status = "active"
        self._transfer_path = pending.transfer_path
        self._transfer_error = ""
        self._on_changed()
        return True

    def reject_incoming(self) -> bool:
        pending = self._incoming
        if pending is None:
            return False
        self._incoming = None
        try:
            pending.invocation.return_dbus_error(
                "org.bluez.obex.Error.Rejected",
                "Rejected by Jugoo",
            )
        except GLib.Error:
            return False
        self._on_changed()
        return True

    def _register_agent(self) -> bool:
        assert self._bus is not None
        interface = self._node_info.interfaces[0]
        try:
            self._agent_id = self._bus.register_object(
                OBEX_AGENT_PATH,
                interface,
                self._on_method_call,
                None,
                None,
            )
            self._bus.call_sync(
                OBEX_BUS_NAME,
                OBEX_ROOT_PATH,
                OBEX_AGENT_MANAGER_INTERFACE,
                "RegisterAgent",
                GLib.Variant("(o)", (OBEX_AGENT_PATH,)),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            _logger.warning("OBEX agent registration failed: %s", exc.message)
            self._unregister_agent()
            # Client send can still work without a receive agent.
            return True

    def _unregister_agent(self) -> None:
        if self._bus is None:
            self._agent_id = 0
            return
        try:
            self._bus.call_sync(
                OBEX_BUS_NAME,
                OBEX_ROOT_PATH,
                OBEX_AGENT_MANAGER_INTERFACE,
                "UnregisterAgent",
                GLib.Variant("(o)", (OBEX_AGENT_PATH,)),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error:
            pass
        if self._agent_id:
            try:
                self._bus.unregister_object(self._agent_id)
            except GLib.Error:
                pass
        self._agent_id = 0

    def _subscribe_transfer_signals(self) -> None:
        assert self._bus is not None
        self._signal_ids.append(
            self._bus.signal_subscribe(
                OBEX_BUS_NAME,
                PROPERTIES_INTERFACE,
                "PropertiesChanged",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_properties_changed,
                None,
            )
        )

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
        if iface != OBEX_TRANSFER_INTERFACE:
            return
        if self._transfer_path and str(object_path) != self._transfer_path:
            return
        self._apply_transfer_props(str(object_path), _props_dict(changed))
        if self._transfer_status in {"complete", "error"}:
            self._cleanup_session(keep_status=True)
        self._on_changed()

    def _apply_transfer_props(self, path: str, props: dict[str, Any]) -> None:
        self._transfer_path = path
        if "Status" in props:
            self._transfer_status = str(props["Status"] or "")
        if "Name" in props and props["Name"]:
            self._transfer_name = str(props["Name"])
        if "Filename" in props and props["Filename"] and not self._transfer_name:
            self._transfer_name = Path(str(props["Filename"])).name
        if "Size" in props and props["Size"] is not None:
            self._transfer_size = int(props["Size"])
        if "Transferred" in props and props["Transferred"] is not None:
            self._transfer_done = int(props["Transferred"])
        if self._transfer_status == "error" and not self._transfer_error:
            self._transfer_error = "Error en la transferencia"

    def _cleanup_session(self, *, keep_status: bool = False) -> None:
        if self._bus is not None and self._session_path:
            try:
                self._bus.call_sync(
                    OBEX_BUS_NAME,
                    OBEX_ROOT_PATH,
                    OBEX_CLIENT_INTERFACE,
                    "RemoveSession",
                    GLib.Variant("(o)", (self._session_path,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    DBUS_TIMEOUT_MS,
                    None,
                )
            except GLib.Error:
                pass
        self._session_path = ""
        self._transfer_path = ""
        if not keep_status:
            self._clear_transfer_state()

    def _clear_transfer_state(self) -> None:
        self._transfer_direction = ""
        self._transfer_name = ""
        self._transfer_status = ""
        self._transfer_size = None
        self._transfer_done = 0
        self._transfer_error = ""
        self._transfer_device = ""

    def _on_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name == "Release":
            invocation.return_value(None)
            return
        if method_name == "Cancel":
            self._incoming = None
            invocation.return_value(None)
            self._on_changed()
            return
        if method_name != "AuthorizePush":
            invocation.return_dbus_error(
                "org.bluez.obex.Error.Rejected",
                f"Unsupported method {method_name}",
            )
            return

        transfer_path = str(parameters.unpack()[0])
        props = self._read_transfer_props(transfer_path)
        name = str(props.get("Name") or Path(str(props.get("Filename") or "archivo")).name)
        size = int(props["Size"]) if props.get("Size") is not None else None
        download = default_download_dir()
        suggested = str(download / sanitize_filename(name))
        if self._incoming is not None:
            self.reject_incoming()
        self._incoming = _IncomingPush(
            transfer_path=transfer_path,
            name=sanitize_filename(name),
            size=size,
            suggested_path=suggested,
            invocation=invocation,
        )
        self._transfer_device = ""
        self._on_incoming()
        self._on_changed()

    def _read_transfer_props(self, path: str) -> dict[str, Any]:
        if self._bus is None:
            return {}
        try:
            result = self._bus.call_sync(
                OBEX_BUS_NAME,
                path,
                PROPERTIES_INTERFACE,
                "GetAll",
                GLib.Variant("(s)", (OBEX_TRANSFER_INTERFACE,)),
                GLib.VariantType("(a{sv})"),
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MS,
                None,
            )
            return _props_dict(result.unpack()[0])
        except GLib.Error as exc:
            _logger.debug("GetAll Transfer1 failed: %s", exc.message)
            return {}
