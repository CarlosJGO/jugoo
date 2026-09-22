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
    _human_label,
    _looks_like_sni_address,
    _parse_service_address,
    _parse_sni_methods_from_introspection,
    _property_icon_pixbuf,
    _resolve_named_icon,
    _snapshot_from_proxy,
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


def test_tray_service_refreshes_on_property_changes() -> None:
    service = SystemTrayService()
    assert service._should_refresh_signal("NewIcon") is True
    assert service._should_refresh_signal("NewToolTip") is True
    assert service._should_refresh_signal("NewTitle") is True
    assert service._should_refresh_signal("g-properties-changed") is True
    assert service._should_refresh_signal("SomeOtherSignal") is False


def test_human_labels_hide_dbus_noise() -> None:
    noise = "org.freedesktop.StatusNotifierItem-656934-1/StatusNotifierItem"
    assert _looks_like_sni_address(noise) is True
    assert _human_label(noise) == ""
    assert _human_label("Unity Hub") == "Unity Hub"
    assert _human_label(":1.42/StatusNotifierItem") == ""


def test_snapshot_never_exposes_dbus_address_as_tooltip() -> None:
    address = "org.freedesktop.StatusNotifierItem-1-1/StatusNotifierItem"
    proxy = _FakeProxy({})
    snapshot = _snapshot_from_proxy(
        address,
        "org.freedesktop.StatusNotifierItem-1-1",
        "/StatusNotifierItem",
        proxy,
        frozenset(),
        props={},
    )
    assert "StatusNotifierItem" not in snapshot.tooltip
    assert snapshot.tooltip == ""


def test_snapshot_uses_title_and_theme_icon(tmp_path) -> None:
    icon_path = tmp_path / "unityhub.png"
    # 1x1 ARGB PNG via GdkPixbuf save
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
    pixbuf.fill(0xFF0000FF)
    pixbuf.savev(str(icon_path), "png", [], [])

    proxy = _FakeProxy({})
    snapshot = _snapshot_from_proxy(
        "org.freedesktop.StatusNotifierItem-9-1/StatusNotifierItem",
        "org.freedesktop.StatusNotifierItem-9-1",
        "/StatusNotifierItem",
        proxy,
        frozenset({"Activate"}),
        props={
            "Id": "unityhub",
            "Title": "Unity Hub",
            "IconName": "unityhub",
            "IconThemePath": str(tmp_path),
            "Status": "Active",
        },
    )
    assert snapshot.tooltip == "Unity Hub"
    assert snapshot.title == "Unity Hub"
    assert snapshot.icon_pixbuf is not None
    assert snapshot.icon_name is None  # pixbuf wins


def test_snapshot_prefers_tooltip_over_chromium_id() -> None:
    """Electron trays often leave Title empty and put the name only in ToolTip."""
    proxy = _FakeProxy({})
    snapshot = _snapshot_from_proxy(
        "org.freedesktop.StatusNotifierItem-656934-1/StatusNotifierItem",
        "org.freedesktop.StatusNotifierItem-656934-1",
        "/StatusNotifierItem",
        proxy,
        frozenset({"Activate"}),
        props={
            "Id": "unityhub_status_icon_1",
            "Title": "",
            "ToolTip": ("", [], "Unity Hub", ""),
            "Status": "Active",
        },
    )
    assert snapshot.tooltip == "Unity Hub"
    assert snapshot.title == "Unity Hub"
    assert "StatusNotifierItem" not in snapshot.tooltip
    assert "StatusNotifierItem" not in snapshot.title


def test_resolve_named_icon_absolute_path(tmp_path) -> None:
    icon_path = tmp_path / "app.png"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
    pixbuf.fill(0x00FF00FF)
    pixbuf.savev(str(icon_path), "png", [], [])
    loaded = _resolve_named_icon(str(icon_path), "")
    assert loaded is not None
    assert loaded.get_width() == 2


if __name__ == "__main__":
    test_parse_service_address()
    test_parse_sni_methods_from_introspection()
    test_argb32_to_rgba()
    test_icon_pixbuf_from_argb()
    test_normalize_sni_address_accepts_bus_name_only()
    test_tray_service_starts_in_recovery_enabled_state()
    test_tray_service_refreshes_on_property_changes()
    test_human_labels_hide_dbus_noise()
    test_snapshot_never_exposes_dbus_address_as_tooltip()
    print("tray safe tests OK")
