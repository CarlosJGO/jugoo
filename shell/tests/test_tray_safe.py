"""Safe tests for tray pixmap parsing (no D-Bus, no real tray apps)."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import GdkPixbuf, GLib

from shell.servicios.bandeja.tray import (
    SystemTrayService,
    _argb32_to_rgba,
    _parse_service_address,
    _parse_sni_methods_from_introspection,
    _property_icon_pixbuf,
    normalize_sni_address,
)


class _FakeProxy:
    def __init__(self, props: dict[str, GLib.Variant]) -> None:
        self._props = props

    def get_cached_property(self, name: str):
        return self._props.get(name)


def test_parse_service_address() -> None:
    bus, path = _parse_service_address(":1.220/StatusNotifierItem")
    assert bus == ":1.220"
    assert path == "/StatusNotifierItem"

    bus, path = _parse_service_address(":1.628/org/ayatana/NotificationItem/steam")
    assert bus == ":1.628"
    assert path == "/org/ayatana/NotificationItem/steam"


def test_parse_sni_methods_from_introspection() -> None:
    xml = """
    <node>
      <interface name="org.kde.StatusNotifierItem">
        <method name="ContextMenu"/>
        <method name="Scroll"/>
      </interface>
    </node>
    """
    methods = _parse_sni_methods_from_introspection(xml)
    assert methods == frozenset({"ContextMenu", "Scroll"})
    assert "Activate" not in methods


def test_argb32_to_rgba() -> None:
    argb = bytes([200, 10, 20, 30])
    assert _argb32_to_rgba(argb) == bytes([10, 20, 30, 200])


def test_icon_pixbuf_from_argb() -> None:
    width, height = 2, 2
    data = bytes([
        255, 0, 0, 255,
        0, 255, 0, 255,
        0, 0, 255, 255,
        255, 255, 255, 255,
    ])
    variant = GLib.Variant("a(iiay)", [(width, height, data)])
    proxy = _FakeProxy({"IconPixmap": variant})
    pixbuf = _property_icon_pixbuf(proxy, "IconPixmap")
    assert pixbuf is not None
    assert pixbuf.get_width() == width
    assert pixbuf.get_height() == height
    assert isinstance(pixbuf, GdkPixbuf.Pixbuf)


def test_normalize_sni_address_accepts_bus_name_only() -> None:
    assert normalize_sni_address(":1.220/StatusNotifierItem") == ":1.220/StatusNotifierItem"
    assert (
        normalize_sni_address("org.kde.StatusNotifierItem-1234-1")
        == "org.kde.StatusNotifierItem-1234-1/StatusNotifierItem"
    )
    assert (
        normalize_sni_address("/StatusNotifierItem", sender=":1.9")
        == ":1.9/StatusNotifierItem"
    )


def test_tray_service_starts_in_recovery_enabled_state() -> None:
    service = SystemTrayService()
    assert service._started is False
    assert service._closing is False
    assert service._watcher_watch_id == 0


if __name__ == "__main__":
    test_parse_service_address()
    test_parse_sni_methods_from_introspection()
    test_argb32_to_rgba()
    test_icon_pixbuf_from_argb()
    test_normalize_sni_address_accepts_bus_name_only()
    test_tray_service_starts_in_recovery_enabled_state()
    print("tray safe tests OK")
