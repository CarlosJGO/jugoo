"""BatteryService / sysfs / UPower parsing tests (mocked hardware)."""

from __future__ import annotations

from pathlib import Path

from shell.eventbus import EventBus
from shell.models import BatterySnapshot
from shell.servicios.energia.battery import (
    BatteryService,
    battery_icon_name,
    battery_should_show,
    build_battery_tooltip,
    read_sysfs_battery,
    snapshot_from_upower_props,
)
from shell.settings.schema import settings_by_key


def _write(path: Path, name: str, value: str) -> None:
    target = path / name
    target.write_text(value + "\n", encoding="utf-8")


def _make_bat(root: Path, name: str, *, capacity: int, status: str, present: str = "1") -> None:
    bat = root / name
    bat.mkdir()
    _write(bat, "type", "Battery")
    _write(bat, "present", present)
    _write(bat, "status", status)
    _write(bat, "capacity", str(capacity))


def _make_ac(root: Path, *, online: bool) -> None:
    ac = root / "AC0"
    ac.mkdir()
    _write(ac, "type", "Mains")
    _write(ac, "online", "1" if online else "0")


def test_no_battery_returns_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    _make_ac(root, online=True)
    snap = read_sysfs_battery(root)
    assert snap.available is False
    assert battery_should_show(snap, "auto") is False
    assert battery_should_show(snap, "never") is False
    assert battery_should_show(snap, "always") is True


def test_discharging_battery(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    _make_ac(root, online=False)
    _make_bat(root, "BAT0", capacity=52, status="Discharging")
    snap = read_sysfs_battery(root)
    assert snap.available is True
    assert snap.percentage == 52
    assert snap.status == "discharging"
    assert snap.charging is False
    assert snap.plugged is False
    assert "52%" in snap.percent_label
    assert battery_should_show(snap, "auto") is True


def test_charging_battery(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    _make_ac(root, online=True)
    _make_bat(root, "BAT0", capacity=40, status="Charging")
    snap = read_sysfs_battery(root)
    assert snap.charging is True
    assert snap.status == "charging"
    assert snap.plugged is True
    assert "charging" in snap.icon_name


def test_full_battery(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    _make_ac(root, online=True)
    _make_bat(root, "BAT0", capacity=100, status="Full")
    snap = read_sysfs_battery(root)
    assert snap.full is True
    assert snap.status == "full"


def test_multiple_batteries_aggregate(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    _make_ac(root, online=False)
    _make_bat(root, "BAT0", capacity=40, status="Discharging")
    _make_bat(root, "BAT1", capacity=60, status="Discharging")
    snap = read_sysfs_battery(root)
    assert snap.available is True
    assert snap.device_count == 2
    assert snap.percentage == 50


def test_absent_bat0_present_bat1(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    _make_bat(root, "BAT0", capacity=10, status="Unknown", present="0")
    _make_bat(root, "BAT1", capacity=77, status="Discharging")
    snap = read_sysfs_battery(root)
    assert snap.available is True
    assert snap.percentage == 77
    assert snap.device_count == 1


def test_read_errors_are_tolerated(tmp_path: Path) -> None:
    root = tmp_path / "power_supply"
    root.mkdir()
    bat = root / "BAT0"
    bat.mkdir()
    _write(bat, "type", "Battery")
    # Missing capacity/status should not raise.
    snap = read_sysfs_battery(root)
    assert snap.available is True
    assert snap.percentage == 0


def test_upower_present_discharging() -> None:
    snap = snapshot_from_upower_props(
        {
            "IsPresent": True,
            "Percentage": 52.0,
            "State": 2,
            "WarningLevel": 1,
            "IconName": "battery-good-symbolic",
            "TimeToEmpty": 5400,
            "EnergyRate": 14.5,
            "Type": 2,
        }
    )
    assert snap.available is True
    assert snap.percentage == 52
    assert snap.status == "discharging"
    assert snap.charging is False
    assert snap.time_to_empty_sec == 5400
    assert snap.source == "upower"
    assert snap.icon_name == "battery-good-symbolic"


def test_upower_charging_and_full() -> None:
    charging = snapshot_from_upower_props(
        {"IsPresent": True, "Percentage": 33.0, "State": 1, "WarningLevel": 1, "Type": 2}
    )
    assert charging.charging is True
    assert charging.status == "charging"
    full = snapshot_from_upower_props(
        {"IsPresent": True, "Percentage": 100.0, "State": 4, "WarningLevel": 1, "Type": 2}
    )
    assert full.full is True
    assert full.status == "full"


def test_upower_absent() -> None:
    snap = snapshot_from_upower_props({"IsPresent": False, "Percentage": 0.0, "State": 0})
    assert snap.available is False


def test_service_with_mock_reader_emits_changes() -> None:
    bus = EventBus(dispatch_on_main=False)
    events: list[BatterySnapshot] = []
    bus.subscribe("battery_changed", events.append)
    state = {"snap": BatterySnapshot.unavailable()}

    def reader() -> BatterySnapshot:
        return state["snap"]

    service = BatteryService(bus, reader=reader, poll_interval_sec=3600)
    # Do not start GLib timer in unit tests; call refresh directly.
    service.refresh()
    assert events[-1].available is False

    state["snap"] = BatterySnapshot(
        available=True,
        percentage=82,
        status="discharging",
        icon_name="battery-good-symbolic",
    )
    service.refresh()
    assert events[-1].percentage == 82
    assert events[-1].available is True

    # Identical snapshot should not re-emit.
    count = len(events)
    service.refresh()
    assert len(events) == count


def test_icon_levels_and_tooltip() -> None:
    assert "caution" in battery_icon_name(
        percentage=5, charging=False, full=False, critical=True, available=True
    )
    assert "charging" in battery_icon_name(
        percentage=50, charging=True, full=False, critical=False, available=True
    )
    assert battery_icon_name(
        percentage=0, charging=False, full=False, critical=False, available=False
    ) == "battery-missing-symbolic"
    snap = BatterySnapshot(
        available=True,
        percentage=12,
        status="discharging",
        low=True,
        icon_name="battery-low-symbolic",
    )
    tip = build_battery_tooltip(snap)
    assert "12%" in tip
    assert "baja" in tip


def test_settings_catalog_has_battery_visibility() -> None:
    assert "widgets.battery_visibility" in settings_by_key()


def main() -> None:
    import tempfile

    cases = (
        test_no_battery_returns_unavailable,
        test_discharging_battery,
        test_charging_battery,
        test_full_battery,
        test_multiple_batteries_aggregate,
        test_absent_bat0_present_bat1,
        test_read_errors_are_tolerated,
    )
    for case in cases:
        with tempfile.TemporaryDirectory() as raw:
            case(Path(raw))
    test_upower_present_discharging()
    test_upower_charging_and_full()
    test_upower_absent()
    test_service_with_mock_reader_emits_changes()
    test_icon_levels_and_tooltip()
    test_settings_catalog_has_battery_visibility()
    print("battery tests OK")


if __name__ == "__main__":
    main()
