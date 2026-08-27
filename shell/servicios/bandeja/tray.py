"""StatusNotifierItem host: discovers tray icons via D-Bus and exposes snapshots to the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import GdkPixbuf, Gio, GLib

WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
SNI_IFACE = "org.kde.StatusNotifierItem"

_STALE_DBUS_MARKERS = (
    "org.freedesktop.DBus.Error.ServiceUnknown",
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.Disconnected",
    "org.freedesktop.DBus.Error.NoReply",
    "org.freedesktop.DBus.Error.NameHasNoOwner",
    "The name is not activatable",
    "Connection closed",
)

_SNI_METHOD_IFACES = frozenset({
    "org.kde.StatusNotifierItem",
    "org.ayatana.StatusNotifierItem",
})

Listener = Callable[[tuple["TrayItemSnapshot", ...]], None]


@dataclass(frozen=True)
class TrayItemSnapshot:
    """Immutable view of one StatusNotifierItem for the widget layer."""

    address: str
    bus_name: str
    object_path: str
    item_id: str
    title: str
    tooltip: str
    status: str
    icon_name: str | None
    icon_pixbuf: GdkPixbuf.Pixbuf | None
    menu_bus: str | None
    menu_path: str | None
    item_is_menu: bool
    supports_activate: bool = False
    supports_secondary_activate: bool = False
    supports_context_menu: bool = False
    supports_scroll: bool = False


@dataclass
class _TrayItemState:
    address: str
    bus_name: str
    object_path: str
    proxy: Gio.DBusProxy
    snapshot: TrayItemSnapshot
    methods: frozenset[str] = field(default_factory=frozenset)
    signal_id: int = 0
    watch_id: int = 0
    name_vanished_id: int = 0


class SystemTrayService:
    """Registers as a StatusNotifierHost and tracks SNI items via D-Bus signals."""

    def __init__(self) -> None:
        self._listener: Listener | None = None
        self._bus: Gio.DBusConnection | None = None
        self._host_name: str | None = None
        self._bus_owner_id: int = 0
        self._items: dict[str, _TrayItemState] = {}
        self._register_signal_id: int = 0
        self._unregister_signal_id: int = 0
        self._watcher_watch_id: int = 0
        self._retry_source_id: int = 0
        self._closing = False
        self._started = False

    def set_listener(self, listener: Listener | None) -> None:
        self._listener = listener
        if listener is not None:
            listener(self.snapshots)

    @property
    def snapshots(self) -> tuple[TrayItemSnapshot, ...]:
        return tuple(state.snapshot for state in self._items.values())

    def start(self) -> None:
        if self._started:
            return
        self._closing = False
        self._started = True
        pid = os.getpid()
        host_name = f"org.freedesktop.StatusNotifierHost-{pid}"
        self._host_name = host_name
        self._bus_owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            host_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        self._watcher_watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            WATCHER_BUS,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            self._on_watcher_vanished,
        )

    def close(self) -> None:
        self._closing = True
        if self._retry_source_id:
            GLib.source_remove(self._retry_source_id)
            self._retry_source_id = 0
        if self._watcher_watch_id:
            Gio.bus_unwatch_name(self._watcher_watch_id)
            self._watcher_watch_id = 0
        for address in list(self._items):
            self._remove_item(address)
        self._unsubscribe_watcher_signals()
        if self._bus_owner_id:
            Gio.bus_unown_name(self._bus_owner_id)
            self._bus_owner_id = 0
        self._bus = None
        self._started = False

    def activate(self, address: str, x: int, y: int) -> bool:
        return self._call_item_method(address, "Activate", GLib.Variant("(ii)", (x, y)))

    def secondary_activate(self, address: str, x: int, y: int) -> bool:
        return self._call_item_method(
            address,
            "SecondaryActivate",
            GLib.Variant("(ii)", (x, y)),
        )

    def context_menu(self, address: str, x: int, y: int) -> bool:
        return self._call_item_method(address, "ContextMenu", GLib.Variant("(ii)", (x, y)))

    def scroll(self, address: str, delta: int, orientation: int) -> bool:
        return self._call_item_method(
            address,
            "Scroll",
            GLib.Variant("(di)", (delta, orientation)),
        )

    def _on_bus_acquired(self, _bus: Gio.DBusConnection, _name: str) -> None:
        self._bus = _bus

    def _on_name_acquired(self, _bus: Gio.DBusConnection, _name: str) -> None:
        if self._closing:
            return
        self._register_with_watcher()

    def _register_with_watcher(self) -> bool:
        if self._closing or self._bus is None:
            return False
        self._unsubscribe_watcher_signals()
        try:
            self._bus.call_sync(
                WATCHER_BUS,
                WATCHER_PATH,
                WATCHER_IFACE,
                "RegisterStatusNotifierHost",
                GLib.Variant("(s)", (self._host_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
            )
        except GLib.Error as error:
            print(f"shell: could not register StatusNotifierHost: {error.message}")
            self._schedule_registration_retry()
            return False

        self._register_signal_id = self._bus.signal_subscribe(
            WATCHER_BUS,
            WATCHER_IFACE,
            "StatusNotifierItemRegistered",
            WATCHER_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_item_registered_signal,
        )
        self._unregister_signal_id = self._bus.signal_subscribe(
            WATCHER_BUS,
            WATCHER_IFACE,
            "StatusNotifierItemUnregistered",
            WATCHER_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_item_unregistered_signal,
        )

        try:
            variant = self._bus.call_sync(
                WATCHER_BUS,
                WATCHER_PATH,
                WATCHER_IFACE,
                "GetRegisteredItems",
                None,
                GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE,
                5000,
            )
        except GLib.Error:
            self._schedule_registration_retry()
            return False

        addresses = variant.unpack()[0]
        for address in addresses:
            self._add_item(str(address))
        return False

    def _on_name_lost(self, *_args) -> None:
        if self._closing:
            return
        self._bus_owner_id = 0
        self._unsubscribe_watcher_signals()
        self._clear_items()
        self._bus = None
        self._schedule_ownership_retry()

    def _on_watcher_appeared(
        self,
        _connection: Gio.DBusConnection,
        _name: str,
        _name_owner: str,
    ) -> None:
        GLib.idle_add(self._register_with_watcher)

    def _on_watcher_vanished(
        self,
        _connection: Gio.DBusConnection,
        _name: str,
    ) -> None:
        GLib.idle_add(self._handle_watcher_vanished)

    def _handle_watcher_vanished(self) -> bool:
        if self._closing:
            return False
        self._unsubscribe_watcher_signals()
        self._clear_items()
        return False

    def _unsubscribe_watcher_signals(self) -> None:
        if self._bus is None:
            self._register_signal_id = 0
            self._unregister_signal_id = 0
            return
        if self._register_signal_id:
            self._bus.signal_unsubscribe(self._register_signal_id)
            self._register_signal_id = 0
        if self._unregister_signal_id:
            self._bus.signal_unsubscribe(self._unregister_signal_id)
            self._unregister_signal_id = 0

    def _clear_items(self) -> None:
        for address in list(self._items):
            self._remove_item(address)

    def _schedule_registration_retry(self) -> None:
        if self._retry_source_id or self._closing:
            return
        self._retry_source_id = GLib.timeout_add(1000, self._retry_registration)

    def _retry_registration(self) -> bool:
        self._retry_source_id = 0
        return self._register_with_watcher()

    def _schedule_ownership_retry(self) -> None:
        if self._retry_source_id or self._closing:
            return
        self._retry_source_id = GLib.timeout_add(1000, self._retry_ownership)

    def _retry_ownership(self) -> bool:
        self._retry_source_id = 0
        if self._closing or not self._started or self._bus_owner_id:
            return False
        self._bus_owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            self._host_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        return False

    def _on_item_registered_signal(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        params: GLib.Variant,
    ) -> None:
        address = params.unpack()[0]
        GLib.idle_add(self._add_item, str(address))

    def _on_item_unregistered_signal(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        params: GLib.Variant,
    ) -> None:
        address = params.unpack()[0]
        GLib.idle_add(self._remove_item, str(address))

    def _add_item(self, address: str) -> bool:
        if address in self._items:
            self._refresh_item(address)
            return False

        try:
            bus_name, object_path = _parse_service_address(address)
        except ValueError as error:
            print(f"shell: {error}")
            return False

        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES,
                None,
                bus_name,
                object_path,
                SNI_IFACE,
                None,
            )
        except GLib.Error as error:
            print(f"shell: could not connect to SNI {address}: {error.message}")
            return False

        methods = _discover_sni_methods(self._bus, bus_name, object_path)
        snapshot = _snapshot_from_proxy(
            address,
            bus_name,
            object_path,
            proxy,
            methods,
        )
        signal_id = proxy.connect("g-signal", self._on_item_proxy_signal, address)

        def _on_bus_name_vanished(_connection: Gio.DBusConnection, _name: str) -> None:
            state = self._items.get(address)
            if state is not None:
                state.watch_id = 0
            GLib.idle_add(self._remove_item, address)

        watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            bus_name,
            Gio.BusNameWatcherFlags.NONE,
            None,
            _on_bus_name_vanished,
        )

        def _on_proxy_owner_changed(_proxy: Gio.DBusProxy, _pspec: object) -> None:
            if not _proxy.get_name_owner():
                state = self._items.get(address)
                if state is not None:
                    state.watch_id = 0
                GLib.idle_add(self._remove_item, address)

        name_vanished_id = proxy.connect("notify::g-name-owner", _on_proxy_owner_changed)

        self._items[address] = _TrayItemState(
            address=address,
            bus_name=bus_name,
            object_path=object_path,
            proxy=proxy,
            snapshot=snapshot,
            methods=methods,
            signal_id=signal_id,
            watch_id=watch_id,
            name_vanished_id=name_vanished_id,
        )
        self._notify_listener()
        return False

    def _remove_item(self, address: str) -> bool:
        state = self._items.pop(address, None)
        if state is None:
            try:
                bus_name, object_path = _parse_service_address(address)
            except ValueError:
                return False
            for key, candidate in list(self._items.items()):
                if (
                    candidate.bus_name == bus_name
                    and candidate.object_path == object_path
                ):
                    state = self._items.pop(key)
                    break
        if state is None:
            return False
        if state.signal_id:
            state.proxy.disconnect(state.signal_id)
        if state.name_vanished_id:
            state.proxy.disconnect(state.name_vanished_id)
        if state.watch_id:
            GLib.source_remove(state.watch_id)
        self._notify_listener()
        return False

    def _refresh_item(self, address: str) -> None:
        state = self._items.get(address)
        if state is None:
            return
        state.snapshot = _snapshot_from_proxy(
            state.address,
            state.bus_name,
            state.object_path,
            state.proxy,
            state.methods,
        )
        self._notify_listener()

    def _on_item_proxy_signal(
        self,
        _proxy: Gio.DBusProxy,
        _sender: str,
        signal: str,
        _params: GLib.Variant,
        address: str,
    ) -> None:
        if signal in {"NewIcon", "NewToolTip", "NewStatus", "NewAttentionIcon"}:
            GLib.idle_add(self._refresh_item, address)

    def _call_item_method(
        self,
        address: str,
        method: str,
        parameters: GLib.Variant,
    ) -> bool:
        state = self._items.get(address)
        if state is None:
            return False
        if method not in state.methods:
            return False
        try:
            state.proxy.call_sync(
                method,
                parameters,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            return True
        except GLib.Error as error:
            if _is_stale_dbus_error(error):
                GLib.idle_add(self._remove_item, address)
            return False

    def _notify_listener(self) -> None:
        if self._listener is None:
            return
        self._listener(self.snapshots)


def _parse_service_address(address: str) -> tuple[str, str]:
    if "/" not in address:
        raise ValueError(f"invalid SNI address: {address}")
    bus_name, object_path = address.split("/", 1)
    return bus_name, f"/{object_path}"


def _snapshot_from_proxy(
    address: str,
    bus_name: str,
    object_path: str,
    proxy: Gio.DBusProxy,
    methods: frozenset[str],
) -> TrayItemSnapshot:
    item_id = _property_string(proxy, "Id") or address
    title = _property_string(proxy, "Title") or item_id
    status = _property_string(proxy, "Status") or "Active"
    icon_name = _property_string(proxy, "IconName")
    attention_icon = _property_string(proxy, "AttentionIconName")
    if status == "NeedsAttention" and attention_icon:
        icon_name = attention_icon or icon_name

    tooltip = _property_tooltip(proxy) or title
    menu_path = _property_object_path(proxy, "Menu")
    item_is_menu = _property_bool(proxy, "ItemIsMenu")

    pixbuf = _property_icon_pixbuf(proxy, "IconPixmap")
    if pixbuf is None and status == "NeedsAttention":
        pixbuf = _property_icon_pixbuf(proxy, "AttentionIconPixmap")

    return TrayItemSnapshot(
        address=address,
        bus_name=bus_name,
        object_path=object_path,
        item_id=item_id,
        title=title,
        tooltip=tooltip,
        status=status,
        icon_name=icon_name or None,
        icon_pixbuf=pixbuf,
        menu_bus=bus_name if menu_path else None,
        menu_path=menu_path,
        item_is_menu=item_is_menu,
        supports_activate="Activate" in methods,
        supports_secondary_activate="SecondaryActivate" in methods,
        supports_context_menu="ContextMenu" in methods,
        supports_scroll="Scroll" in methods,
    )


def _discover_sni_methods(
    bus: Gio.DBusConnection | None,
    bus_name: str,
    object_path: str,
) -> frozenset[str]:
    if bus is None:
        return frozenset()
    try:
        variant = bus.call_sync(
            bus_name,
            object_path,
            "org.freedesktop.DBus.Introspectable",
            "Introspect",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            3000,
        )
    except GLib.Error:
        return frozenset()
    return _parse_sni_methods_from_introspection(variant.unpack()[0])


def _parse_sni_methods_from_introspection(xml: str) -> frozenset[str]:
    methods: set[str] = set()
    in_iface = False
    for line in xml.splitlines():
        iface_match = re.search(r'interface name="([^"]+)"', line)
        if iface_match:
            in_iface = iface_match.group(1) in _SNI_METHOD_IFACES
            continue
        if not in_iface:
            continue
        method_match = re.search(r'<method name="([^"]+)"', line)
        if method_match:
            methods.add(method_match.group(1))
    return frozenset(methods)


def _is_stale_dbus_error(error: GLib.Error) -> bool:
    message = error.message
    return any(marker in message for marker in _STALE_DBUS_MARKERS)


def _property_string(proxy: Gio.DBusProxy, name: str) -> str:
    value = proxy.get_cached_property(name)
    if value is None:
        return ""
    unpacked = value.unpack()
    return str(unpacked) if unpacked is not None else ""


def _property_bool(proxy: Gio.DBusProxy, name: str) -> bool:
    value = proxy.get_cached_property(name)
    if value is None:
        return False
    return bool(value.unpack())


def _property_object_path(proxy: Gio.DBusProxy, name: str) -> str | None:
    value = proxy.get_cached_property(name)
    if value is None:
        return None
    path = value.unpack()
    if not path or path == "/":
        return None
    return str(path)


def _property_tooltip(proxy: Gio.DBusProxy) -> str:
    value = proxy.get_cached_property("ToolTip")
    if value is None:
        return ""
    unpacked = value.unpack()
    if not isinstance(unpacked, tuple) or len(unpacked) < 4:
        return ""
    title = str(unpacked[2] or "").strip()
    subtitle = str(unpacked[3] or "").strip()
    if title and subtitle and subtitle != title:
        return f"{title}\n{subtitle}"
    return title or subtitle


def _property_icon_pixbuf(proxy: Gio.DBusProxy, name: str) -> GdkPixbuf.Pixbuf | None:
    value = proxy.get_cached_property(name)
    if value is None:
        return None
    pixmaps = value.unpack()
    if not pixmaps:
        return None

    best: tuple[int, int, bytes] | None = None
    best_score = -1
    for entry in pixmaps:
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        width, height, data = int(entry[0]), int(entry[1]), bytes(entry[2])
        if width <= 0 or height <= 0 or not data:
            continue
        score = min(width, height)
        if score > best_score:
            best_score = score
            best = (width, height, data)

    if best is None:
        return None

    width, height, data = best
    expected = width * height * 4
    if len(data) < expected:
        return None
    rgba = _argb32_to_rgba(data[:expected])
    try:
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(rgba),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            width,
            height,
            width * 4,
        )
    except GLib.Error:
        return None


def _argb32_to_rgba(data: bytes) -> bytes:
    """Convert SNI ARGB32 pixel bytes to GdkPixbuf RGBA order."""
    out = bytearray(len(data))
    for index in range(0, len(data), 4):
        alpha = data[index]
        red = data[index + 1]
        green = data[index + 2]
        blue = data[index + 3]
        out[index : index + 4] = bytes((red, green, blue, alpha))
    return bytes(out)
