"""Minimal BlueZ Agent1 exported on the system bus for pairing flows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

_logger = logging.getLogger(__name__)

BLUEZ_BUS_NAME = "org.bluez"
AGENT_MANAGER_PATH = "/org/bluez"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_OBJECT_PATH = "/com/jugoo/shell/bluetooth/agent"
AGENT_CAPABILITY = "DisplayYesNo"

# XML introspection for Gio.DBusConnection.register_object.
_AGENT_XML = """
<node>
  <interface name="org.bluez.Agent1">
    <method name="Release"/>
    <method name="RequestPinCode">
      <arg direction="in" type="o" name="device"/>
      <arg direction="out" type="s" name="pin"/>
    </method>
    <method name="DisplayPinCode">
      <arg direction="in" type="o" name="device"/>
      <arg direction="in" type="s" name="pincode"/>
    </method>
    <method name="RequestPasskey">
      <arg direction="in" type="o" name="device"/>
      <arg direction="out" type="u" name="passkey"/>
    </method>
    <method name="DisplayPasskey">
      <arg direction="in" type="o" name="device"/>
      <arg direction="in" type="u" name="passkey"/>
      <arg direction="in" type="q" name="entered"/>
    </method>
    <method name="RequestConfirmation">
      <arg direction="in" type="o" name="device"/>
      <arg direction="in" type="u" name="passkey"/>
    </method>
    <method name="RequestAuthorization">
      <arg direction="in" type="o" name="device"/>
    </method>
    <method name="AuthorizeService">
      <arg direction="in" type="o" name="device"/>
      <arg direction="in" type="s" name="uuid"/>
    </method>
    <method name="Cancel"/>
  </interface>
</node>
"""


@dataclass
class _PendingInvocation:
    kind: str
    device_path: str
    device_address: str
    device_name: str
    passkey: int | None
    invocation: Gio.DBusMethodInvocation
    hint: str = ""


class BluetoothAgent:
    """Registers org.bluez.Agent1 and forwards interactive challenges to the service."""

    def __init__(
        self,
        *,
        lookup_device: Callable[[str], tuple[str, str]],
        on_challenge: Callable[[_PendingInvocation], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        self._lookup_device = lookup_device
        self._on_challenge = on_challenge
        self._on_cancelled = on_cancelled
        self._bus: Gio.DBusConnection | None = None
        self._registration_id = 0
        self._pending: _PendingInvocation | None = None
        self._node_info = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML)

    @property
    def pending(self) -> _PendingInvocation | None:
        return self._pending

    def register(self, bus: Gio.DBusConnection) -> bool:
        self.unregister()
        self._bus = bus
        interface = self._node_info.interfaces[0]
        try:
            self._registration_id = bus.register_object(
                AGENT_OBJECT_PATH,
                interface,
                self._on_method_call,
                None,
                None,
            )
        except GLib.Error as exc:
            _logger.warning("Bluetooth agent register_object failed: %s", exc.message)
            self._registration_id = 0
            return False

        try:
            bus.call_sync(
                BLUEZ_BUS_NAME,
                AGENT_MANAGER_PATH,
                AGENT_MANAGER_INTERFACE,
                "RegisterAgent",
                GLib.Variant("(os)", (AGENT_OBJECT_PATH, AGENT_CAPABILITY)),
                None,
                Gio.DBusCallFlags.NONE,
                3_000,
                None,
            )
            bus.call_sync(
                BLUEZ_BUS_NAME,
                AGENT_MANAGER_PATH,
                AGENT_MANAGER_INTERFACE,
                "RequestDefaultAgent",
                GLib.Variant("(o)", (AGENT_OBJECT_PATH,)),
                None,
                Gio.DBusCallFlags.NONE,
                3_000,
                None,
            )
        except GLib.Error as exc:
            _logger.warning("Bluetooth AgentManager registration failed: %s", exc.message)
            self.unregister()
            return False
        return True

    def unregister(self) -> None:
        self.reject_pending()
        if self._bus is not None and self._registration_id:
            try:
                self._bus.call_sync(
                    BLUEZ_BUS_NAME,
                    AGENT_MANAGER_PATH,
                    AGENT_MANAGER_INTERFACE,
                    "UnregisterAgent",
                    GLib.Variant("(o)", (AGENT_OBJECT_PATH,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    3_000,
                    None,
                )
            except GLib.Error:
                pass
            try:
                self._bus.unregister_object(self._registration_id)
            except GLib.Error:
                pass
        self._registration_id = 0
        self._bus = None

    def accept_pending(self, *, pin: str | None = None, passkey: int | None = None) -> bool:
        pending = self._pending
        if pending is None:
            return False
        self._pending = None
        try:
            if pending.kind == "pin":
                pending.invocation.return_value(GLib.Variant("(s)", (pin or "",)))
            elif pending.kind == "passkey":
                value = 0 if passkey is None else int(passkey)
                pending.invocation.return_value(GLib.Variant("(u)", (value,)))
            else:
                pending.invocation.return_value(None)
        except GLib.Error as exc:
            _logger.debug("Bluetooth agent accept failed: %s", exc.message)
            return False
        return True

    def reject_pending(self) -> bool:
        pending = self._pending
        if pending is None:
            return False
        self._pending = None
        try:
            pending.invocation.return_dbus_error(
                "org.bluez.Error.Rejected",
                "Rejected by Jugoo",
            )
        except GLib.Error:
            return False
        return True

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
        args = parameters.unpack() if parameters is not None else ()
        if method_name == "Release":
            invocation.return_value(None)
            return
        if method_name == "Cancel":
            self._pending = None
            self._on_cancelled()
            invocation.return_value(None)
            return
        if method_name == "DisplayPinCode":
            device_path, pincode = args
            address, name = self._lookup_device(str(device_path))
            self._queue_challenge(
                kind="display_pin",
                device_path=str(device_path),
                device_address=address,
                device_name=name,
                passkey=None,
                invocation=invocation,
                hint=f"PIN: {pincode}",
                auto_complete=True,
            )
            return
        if method_name == "DisplayPasskey":
            device_path, passkey, _entered = args
            address, name = self._lookup_device(str(device_path))
            self._queue_challenge(
                kind="display_passkey",
                device_path=str(device_path),
                device_address=address,
                device_name=name,
                passkey=int(passkey),
                invocation=invocation,
                hint=f"Código: {int(passkey):06d}",
                auto_complete=True,
            )
            return
        if method_name == "RequestConfirmation":
            device_path, passkey = args
            address, name = self._lookup_device(str(device_path))
            self._queue_challenge(
                kind="confirmation",
                device_path=str(device_path),
                device_address=address,
                device_name=name,
                passkey=int(passkey),
                invocation=invocation,
                hint=f"Confirmar código {int(passkey):06d}",
            )
            return
        if method_name == "RequestAuthorization":
            device_path = args[0]
            address, name = self._lookup_device(str(device_path))
            self._queue_challenge(
                kind="authorization",
                device_path=str(device_path),
                device_address=address,
                device_name=name,
                passkey=None,
                invocation=invocation,
                hint="Autorizar emparejamiento",
            )
            return
        if method_name == "AuthorizeService":
            # Trusted / already-paired devices: allow services without UI.
            invocation.return_value(None)
            return
        if method_name == "RequestPinCode":
            device_path = args[0]
            address, name = self._lookup_device(str(device_path))
            self._queue_challenge(
                kind="pin",
                device_path=str(device_path),
                device_address=address,
                device_name=name,
                passkey=None,
                invocation=invocation,
                hint="Introduce el PIN del dispositivo",
            )
            return
        if method_name == "RequestPasskey":
            device_path = args[0]
            address, name = self._lookup_device(str(device_path))
            self._queue_challenge(
                kind="passkey",
                device_path=str(device_path),
                device_address=address,
                device_name=name,
                passkey=None,
                invocation=invocation,
                hint="Introduce el código numérico",
            )
            return
        invocation.return_dbus_error(
            "org.bluez.Error.Rejected",
            f"Unsupported agent method {method_name}",
        )

    def _queue_challenge(
        self,
        *,
        kind: str,
        device_path: str,
        device_address: str,
        device_name: str,
        passkey: int | None,
        invocation: Gio.DBusMethodInvocation,
        hint: str,
        auto_complete: bool = False,
    ) -> None:
        if self._pending is not None:
            self.reject_pending()
        if auto_complete:
            # Display-only methods: acknowledge immediately; UI shows an ephemeral hint.
            try:
                invocation.return_value(None)
            except GLib.Error:
                pass
            self._on_challenge(
                _PendingInvocation(
                    kind=kind,
                    device_path=device_path,
                    device_address=device_address,
                    device_name=device_name,
                    passkey=passkey,
                    invocation=invocation,
                    hint=hint,
                )
            )
            return

        pending = _PendingInvocation(
            kind=kind,
            device_path=device_path,
            device_address=device_address,
            device_name=device_name,
            passkey=passkey,
            invocation=invocation,
            hint=hint,
        )
        self._pending = pending
        self._on_challenge(pending)
