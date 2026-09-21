"""Battery monitoring via UPower (preferred) or /sys/class/power_supply."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from ... import config as shell_config
from ...eventbus import EventBus
from ...models import BatterySnapshot

BATTERY_CHANGED = "battery_changed"

UPOWER_BUS = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
UPOWER_IFACE = "org.freedesktop.UPower"
UPOWER_DEVICE_IFACE = "org.freedesktop.UPower.Device"
UPOWER_DISPLAY_PATH = "/org/freedesktop/UPower/devices/DisplayDevice"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"

# UPower DeviceState
_UP_STATE_UNKNOWN = 0
_UP_STATE_CHARGING = 1
_UP_STATE_DISCHARGING = 2
_UP_STATE_EMPTY = 3
_UP_STATE_FULLY_CHARGED = 4
_UP_STATE_PENDING_CHARGE = 5
_UP_STATE_PENDING_DISCHARGE = 6

_UP_WARN_NONE = 1
_UP_WARN_DISCHARGING = 2
_UP_WARN_LOW = 3
_UP_WARN_CRITICAL = 4

BatteryReader = Callable[[], BatterySnapshot]

_LOG = logging.getLogger(__name__)


class BatteryService:
    """Owns battery snapshots and publishes ``battery_changed`` on the EventBus."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        reader: BatteryReader | None = None,
        poll_interval_sec: float | None = None,
        power_supply_dir: Path | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._reader = reader
        self._poll_interval_sec = (
            float(poll_interval_sec)
            if poll_interval_sec is not None
            else float(shell_config.BATTERY_POLL_INTERVAL_SEC)
        )
        self._power_supply_dir = power_supply_dir or Path("/sys/class/power_supply")
        self._snapshot = BatterySnapshot.unavailable()
        self._timer_id = 0
        self._proxy: Gio.DBusProxy | None = None
        self._signal_id = 0
        self._started = False
        self._emitted_once = False

    @property
    def snapshot(self) -> BatterySnapshot:
        return self._snapshot

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._reader is None:
            self._try_bind_upower()
        self.refresh()
        # Fallback / debounce poll even with UPower signals (missed events, sysfs).
        interval_ms = max(5_000, int(self._poll_interval_sec * 1000))
        self._timer_id = GLib.timeout_add(interval_ms, self._on_poll)

    def close(self) -> None:
        self._started = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._proxy is not None and self._signal_id:
            try:
                self._proxy.disconnect(self._signal_id)
            except Exception:
                pass
            self._signal_id = 0
        self._proxy = None

    def refresh(self) -> BatterySnapshot:
        try:
            if self._reader is not None:
                snapshot = self._reader()
            elif self._proxy is not None:
                snapshot = _snapshot_from_upower_proxy(self._proxy)
                if not snapshot.available:
                    snapshot = read_sysfs_battery(self._power_supply_dir)
            else:
                snapshot = read_sysfs_battery(self._power_supply_dir)
        except Exception as error:  # noqa: BLE001 — never crash the bar
            _LOG.debug("battery refresh failed: %s", error)
            snapshot = BatterySnapshot.unavailable()
        self._publish(snapshot)
        return snapshot

    def _on_poll(self) -> bool:
        if not self._started:
            return False
        self.refresh()
        return True

    def _try_bind_upower(self) -> None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.NONE,
                None,
                UPOWER_BUS,
                UPOWER_DISPLAY_PATH,
                UPOWER_DEVICE_IFACE,
                None,
            )
        except Exception as error:  # noqa: BLE001
            _LOG.debug("UPower DisplayDevice unavailable: %s", error)
            self._proxy = None
            return
        self._proxy = proxy
        try:
            self._signal_id = proxy.connect(
                "g-properties-changed",
                self._on_upower_properties_changed,
            )
        except Exception as error:  # noqa: BLE001
            _LOG.debug("UPower signal connect failed: %s", error)
            self._signal_id = 0

    def _on_upower_properties_changed(self, _proxy, _changed, _invalidated) -> None:
        GLib.idle_add(self.refresh)

    def _publish(self, snapshot: BatterySnapshot) -> None:
        if self._emitted_once and snapshot == self._snapshot:
            return
        self._emitted_once = True
        self._snapshot = snapshot
        self._event_bus.emit(BATTERY_CHANGED, snapshot)


def read_sysfs_battery(power_supply_dir: Path | None = None) -> BatterySnapshot:
    """Aggregate Battery + Mains entries under ``/sys/class/power_supply``."""
    root = power_supply_dir or Path("/sys/class/power_supply")
    if not root.is_dir():
        return BatterySnapshot.unavailable()

    batteries: list[dict[str, str]] = []
    mains_online = False
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() and not entry.is_symlink():
            continue
        type_path = entry / "type"
        if not type_path.is_file():
            continue
        kind = _read_text(type_path).strip()
        if kind == "Mains":
            online = _read_text(entry / "online").strip()
            if online in {"1", "yes", "true"}:
                mains_online = True
            continue
        if kind != "Battery":
            continue
        present = _read_text(entry / "present").strip()
        if present in {"0", "no", "false"}:
            continue
        batteries.append(
            {
                "status": _read_text(entry / "status").strip() or "Unknown",
                "capacity": _read_text(entry / "capacity").strip(),
                "energy_now": _read_text(entry / "energy_now").strip(),
                "energy_full": _read_text(entry / "energy_full").strip(),
                "charge_now": _read_text(entry / "charge_now").strip(),
                "charge_full": _read_text(entry / "charge_full").strip(),
            }
        )

    if not batteries:
        return BatterySnapshot.unavailable()

    percentage = _aggregate_percentage(batteries)
    statuses = [item["status"].casefold() for item in batteries]
    any_charging = any(status == "charging" for status in statuses)
    any_full = any(status in {"full", "not charging"} for status in statuses)
    any_discharging = any(status == "discharging" for status in statuses)

    if any_charging:
        status = "charging"
    elif any_full and (mains_online or not any_discharging):
        status = "full"
    elif any_discharging:
        status = "discharging"
    elif mains_online:
        status = "full" if percentage >= 99 else "unknown"
    else:
        status = "unknown"

    charging = status == "charging"
    full = status == "full" or (mains_online and percentage >= 99 and not any_discharging)
    plugged = mains_online or charging or full
    low = percentage <= int(shell_config.BATTERY_LOW_PERCENT) and not charging and not full
    critical = (
        percentage <= int(shell_config.BATTERY_CRITICAL_PERCENT)
        and not charging
        and not full
    )
    icon = battery_icon_name(
        percentage=percentage,
        charging=charging,
        full=full,
        critical=critical,
        available=True,
    )
    return BatterySnapshot(
        available=True,
        percentage=percentage,
        status=status,
        plugged=plugged,
        charging=charging,
        full=full,
        low=low,
        critical=critical,
        icon_name=icon,
        device_count=len(batteries),
        source="sysfs",
    )


def _snapshot_from_upower_proxy(proxy: Gio.DBusProxy) -> BatterySnapshot:
    props = _upower_cached_props(proxy)
    return snapshot_from_upower_props(props)


def snapshot_from_upower_props(props: dict[str, object]) -> BatterySnapshot:
    """Build a snapshot from UPower Device properties (DisplayDevice preferred)."""
    has_is_present = "IsPresent" in props
    is_present = bool(_variant_value(props.get("IsPresent"), False)) if has_is_present else None
    percentage_raw = _variant_value(props.get("Percentage"), None)
    device_type = int(_variant_value(props.get("Type"), 0) or 0)

    if is_present is False:
        return BatterySnapshot.unavailable()
    if is_present is None and device_type not in {0, 2} and percentage_raw is None:
        # Type: 2 = Battery. Unknown/0 may still be DisplayDevice aggregate.
        return BatterySnapshot.unavailable()
    if is_present is None and percentage_raw is None:
        return BatterySnapshot.unavailable()

    try:
        percentage = int(round(float(percentage_raw if percentage_raw is not None else 0)))
    except (TypeError, ValueError):
        percentage = 0
    percentage = max(0, min(100, percentage))

    state = int(_variant_value(props.get("State"), _UP_STATE_UNKNOWN) or 0)
    warning = int(_variant_value(props.get("WarningLevel"), _UP_WARN_NONE) or 0)
    icon_from_up = str(_variant_value(props.get("IconName"), "") or "").strip()

    charging = state in {_UP_STATE_CHARGING, _UP_STATE_PENDING_CHARGE}
    full = state == _UP_STATE_FULLY_CHARGED or (
        state == _UP_STATE_PENDING_DISCHARGE and percentage >= 99
    )
    if state == _UP_STATE_EMPTY:
        status = "empty"
    elif charging:
        status = "charging"
    elif full:
        status = "full"
    elif state == _UP_STATE_DISCHARGING:
        status = "discharging"
    else:
        status = "unknown"

    plugged = charging or full or bool(_variant_value(props.get("Online"), False))
    critical = warning >= _UP_WARN_CRITICAL or (
        percentage <= int(shell_config.BATTERY_CRITICAL_PERCENT)
        and not charging
        and not full
    )
    low = (
        (
            warning >= _UP_WARN_LOW
            or percentage <= int(shell_config.BATTERY_LOW_PERCENT)
        )
        and not charging
        and not full
        and not critical
    )

    icon = icon_from_up or battery_icon_name(
        percentage=percentage,
        charging=charging,
        full=full,
        critical=critical,
        available=True,
    )

    time_to_empty = _optional_positive_int(_variant_value(props.get("TimeToEmpty"), None))
    time_to_full = _optional_positive_int(_variant_value(props.get("TimeToFull"), None))
    energy_rate = _optional_float(_variant_value(props.get("EnergyRate"), None))

    return BatterySnapshot(
        available=True,
        percentage=percentage,
        status=status,
        plugged=plugged,
        charging=charging,
        full=full,
        low=low,
        critical=critical,
        icon_name=icon,
        time_to_empty_sec=time_to_empty,
        time_to_full_sec=time_to_full,
        energy_rate_w=energy_rate,
        device_count=1,
        source="upower",
    )


def battery_icon_name(
    *,
    percentage: int,
    charging: bool,
    full: bool,
    critical: bool,
    available: bool,
) -> str:
    """Freedesktop symbolic battery icon for the given state."""
    if not available:
        return "battery-missing-symbolic"
    if full and not charging:
        return "battery-full-symbolic"
    level = _level_token(percentage, critical=critical)
    if charging:
        return f"battery-{level}-charging-symbolic"
    return f"battery-{level}-symbolic"


def _level_token(percentage: int, *, critical: bool) -> str:
    if critical or percentage < 10:
        return "caution"
    if percentage < 20:
        return "low"
    if percentage < 40:
        return "low"
    if percentage < 60:
        return "medium"
    if percentage < 80:
        return "good"
    return "full"


def battery_should_show(snapshot: BatterySnapshot, visibility: str | None = None) -> bool:
    mode = (visibility or shell_config.BATTERY_VISIBILITY or "auto").strip().casefold()
    if mode == "never":
        return False
    if mode == "always":
        return True
    return bool(snapshot.available)


def build_battery_tooltip(snapshot: BatterySnapshot) -> str:
    if not snapshot.available:
        return "Sin batería"
    parts = [f"Batería {snapshot.percent_label}"]
    if snapshot.charging:
        parts.append("cargando")
    elif snapshot.full:
        parts.append("cargada")
    elif snapshot.plugged:
        parts.append("conectada a corriente")
    else:
        parts.append("descargando")
    if snapshot.critical:
        parts.append("crítica")
    elif snapshot.low:
        parts.append("baja")
    return " · ".join(parts)


def _aggregate_percentage(batteries: list[dict[str, str]]) -> int:
    weighted_sum = 0.0
    weight_total = 0.0
    plain: list[int] = []
    for item in batteries:
        capacity = _parse_int(item.get("capacity"))
        if capacity is not None:
            plain.append(max(0, min(100, capacity)))
        full = _parse_int(item.get("energy_full")) or _parse_int(item.get("charge_full"))
        now = _parse_int(item.get("energy_now")) or _parse_int(item.get("charge_now"))
        if full and full > 0 and now is not None:
            weighted_sum += max(0.0, min(1.0, now / full)) * full
            weight_total += full
    if weight_total > 0:
        return int(round(100.0 * weighted_sum / weight_total))
    if plain:
        return int(round(sum(plain) / len(plain)))
    return 0


def _upower_cached_props(proxy: Gio.DBusProxy) -> dict[str, object]:
    props: dict[str, object] = {}
    for name in (
        "IsPresent",
        "Percentage",
        "State",
        "WarningLevel",
        "IconName",
        "TimeToEmpty",
        "TimeToFull",
        "EnergyRate",
        "Type",
        "Online",
    ):
        try:
            value = proxy.get_cached_property(name)
        except Exception:
            value = None
        if value is not None:
            props[name] = value
    return props


def _variant_value(value: object, default: object) -> object:
    if value is None:
        return default
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return default
    return value


def _optional_positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _optional_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _parse_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
