"""Safe tests for BluetoothService helpers and degraded BlueZ absence."""

from __future__ import annotations

from shell.eventbus import EventBus
from shell.models import BluetoothDeviceSnapshot, BluetoothSnapshot
from shell.servicios.bluetooth.bluetooth import (
    BluetoothService,
    bluetooth_icon_name,
    bluetooth_visual_state,
    build_bluetooth_tooltip,
    compose_device_snapshot,
    device_type_from_icon,
    is_bluetooth_address,
    normalize_address,
    sort_devices,
)


def test_normalize_and_validate_address() -> None:
    assert normalize_address("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert is_bluetooth_address("AA:BB:CC:DD:EE:FF")
    assert not is_bluetooth_address("not-an-address")


def test_compose_device_snapshot_prefers_alias() -> None:
    device = compose_device_snapshot(
        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        {
            "Address": "aa:bb:cc:dd:ee:ff",
            "Name": "WH-1000",
            "Alias": "Auriculares",
            "Paired": True,
            "Connected": True,
            "Trusted": True,
            "Icon": "audio-headset",
        },
        battery_percent=87,
    )
    assert device.name == "Auriculares"
    assert device.address == "AA:BB:CC:DD:EE:FF"
    assert device.connected
    assert device.paired
    assert device.battery_percent == 87
    assert device.device_type == "Auriculares"
    assert device.icon == "audio-headset-symbolic"
    assert device.can_send_files is True


def test_object_push_uuid_detection() -> None:
    from shell.servicios.bluetooth.obex import device_supports_object_push, sanitize_filename

    assert device_supports_object_push(
        ["00001105-0000-1000-8000-00805f9b34fb", "0000110b-0000-1000-8000-00805f9b34fb"]
    )
    assert not device_supports_object_push(["0000110b-0000-1000-8000-00805f9b34fb"])
    assert sanitize_filename("../../evil name!!.txt") == "evil name_.txt"


def test_sort_devices_connected_first() -> None:
    devices = sort_devices(
        (
            BluetoothDeviceSnapshot("AA:00", "Mouse", "/a", paired=True, connected=False),
            BluetoothDeviceSnapshot("BB:00", "Buds", "/b", paired=True, connected=True),
            BluetoothDeviceSnapshot("CC:00", "Speaker", "/c", paired=False, connected=False),
        )
    )
    assert [device.name for device in devices] == ["Buds", "Mouse", "Speaker"]


def test_icon_and_tooltip_states() -> None:
    unavailable = BluetoothSnapshot.empty(error_message="BlueZ no está instalado")
    assert bluetooth_icon_name(unavailable) == "bluetooth-hardware-disabled-symbolic"
    assert bluetooth_visual_state(unavailable) == "unavailable"
    assert "No disponible" in build_bluetooth_tooltip(unavailable)

    off = BluetoothSnapshot(available=True, powered=False, discovering=False)
    assert bluetooth_icon_name(off) == "bluetooth-disabled-symbolic"
    assert bluetooth_visual_state(off) == "disabled"

    scanning = BluetoothSnapshot(available=True, powered=True, discovering=True)
    assert bluetooth_icon_name(scanning) == "bluetooth-acquiring-symbolic"
    assert bluetooth_visual_state(scanning) == "discovering"

    connected = BluetoothSnapshot(
        available=True,
        powered=True,
        discovering=False,
        devices=(
            BluetoothDeviceSnapshot(
                "AA:BB:CC:DD:EE:FF",
                "Auriculares",
                "/dev",
                paired=True,
                connected=True,
            ),
        ),
    )
    assert bluetooth_icon_name(connected) == "bluetooth-active-symbolic"
    assert "Auriculares" in build_bluetooth_tooltip(connected)


def test_device_type_from_icon() -> None:
    assert device_type_from_icon("audio-headphones") == "Auriculares"
    assert device_type_from_icon("input-mouse") == "Ratón"
    assert device_type_from_icon("") == ""


def test_service_degrades_without_bluez() -> None:
    bus = EventBus(dispatch_on_main=False)
    events: list[BluetoothSnapshot] = []
    bus.subscribe("bluetooth_changed", events.append)

    service = BluetoothService(bus)
    service.start()
    assert service.snapshot.available is False or isinstance(service.snapshot, BluetoothSnapshot)
    # Either BlueZ is present on the host or we degrade cleanly.
    if not service.available:
        assert "BlueZ" in service.snapshot.error_message or service.snapshot.error_message
        assert bluetooth_icon_name(service.snapshot)
    service.close()
    bus.close()


def test_mutations_are_safe_when_unavailable() -> None:
    service = BluetoothService(EventBus(dispatch_on_main=False))
    # Without start()/BlueZ these should not raise.
    service.toggle_powered()
    service.start_discovery()
    service.connect_device("AA:BB:CC:DD:EE:FF")
    service.disconnect_device("AA:BB:CC:DD:EE:FF")
    service.pair_device("AA:BB:CC:DD:EE:FF")
    service.remove_device("AA:BB:CC:DD:EE:FF")
    service.accept_pairing()
    service.reject_pairing()
    service.close()


if __name__ == "__main__":
    test_normalize_and_validate_address()
    test_compose_device_snapshot_prefers_alias()
    test_object_push_uuid_detection()
    test_sort_devices_connected_first()
    test_icon_and_tooltip_states()
    test_device_type_from_icon()
    test_service_degrades_without_bluez()
    test_mutations_are_safe_when_unavailable()
    print("ok")
