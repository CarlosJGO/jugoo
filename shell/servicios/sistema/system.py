"""System statistics read from Linux /proc and /sys interfaces.

Sources used (no subprocess, no background threads):

* CPU usage        -- ``/proc/stat`` aggregate line, sampled as a delta between
  two consecutive reads so the value is an interval average, never a snapshot.
* CPU temperature  -- ``hwmon`` device exposed by ``k10temp`` (AMD Zen).  On
  Ryzen 5000 desktop parts ``Tctl`` carries no offset, so it equals ``Tdie``;
  ``Tdie`` is still preferred when the kernel exports it.
* Memory           -- ``/proc/meminfo`` (``MemTotal`` minus ``MemAvailable``).
* GPU temperatures -- ``hwmon`` device exposed by ``amdgpu``: ``edge`` is the
  package temperature used for UI state, ``junction`` (hot spot) and ``mem``
  are collected for future use only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

HWMON_ROOT = Path("/sys/class/hwmon")
DRM_ROOT = Path("/sys/class/drm")
PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")
PCI_IDS_FILES = (
    Path("/usr/share/hwdata/pci.ids"),
    Path("/usr/share/misc/pci.ids"),
)

TEMPERATURE_COLD = "cold"
TEMPERATURE_NORMAL = "normal"
TEMPERATURE_WARM = "warm"
TEMPERATURE_HOT = "hot"

TemperatureRanges = tuple[tuple[float | None, str], ...]

# Ryzen 5 5500: idle 35-50, sustained load 51-75, heavy load 76-85, alert 86+.
CPU_TEMP_RANGES: TemperatureRanges = (
    (50.0, TEMPERATURE_COLD),
    (75.0, TEMPERATURE_NORMAL),
    (85.0, TEMPERATURE_WARM),
    (None, TEMPERATURE_HOT),
)

# Radeon RX 6600 edge temperature: idle 30-49, gaming 50-72, alert 73+.
GPU_TEMP_RANGES: TemperatureRanges = (
    (49.0, TEMPERATURE_COLD),
    (72.0, TEMPERATURE_NORMAL),
    (None, TEMPERATURE_HOT),
)

# The RX 6600 keeps its fans stopped below this edge temperature.
GPU_FAN_START_TEMP = 50.0

CPU_HWMON_NAMES = ("k10temp", "zenpower3", "zenpower", "coretemp")
CPU_TEMP_LABEL_PRIORITY = ("Tdie", "Tctl", "Tccd1", "Package id 0")
GPU_HWMON_NAMES = ("amdgpu",)
GPU_EDGE_LABELS = ("edge", "temp1_input")
GPU_JUNCTION_LABELS = ("junction", "hotspot")
GPU_MEMORY_LABELS = ("mem",)

SENSOR_REDISCOVERY_INTERVAL_SEC = 30.0


def temperature_level(
    temperature_c: float | None,
    ranges: TemperatureRanges,
) -> str | None:
    """Map a temperature to one of the shared thermal state names."""
    if temperature_c is None:
        return None
    for upper_bound, level in ranges:
        if upper_bound is None or temperature_c <= upper_bound:
            return level
    return None


@dataclass(frozen=True)
class CpuStats:
    usage_percent: float | None = None
    temperature_c: float | None = None

    @property
    def temperature_level(self) -> str | None:
        return temperature_level(self.temperature_c, CPU_TEMP_RANGES)


@dataclass(frozen=True)
class MemoryStats:
    used_bytes: int | None = None
    total_bytes: int | None = None

    @property
    def usage_percent(self) -> float | None:
        if not self.total_bytes or self.used_bytes is None:
            return None
        return self.used_bytes / self.total_bytes * 100.0


@dataclass(frozen=True)
class GpuStats:
    name: str | None = None
    temperature_c: float | None = None
    # Collected but intentionally unused by the UI; hot spot must not drive state.
    hotspot_temperature_c: float | None = None
    memory_temperature_c: float | None = None
    vram_used_bytes: int | None = None
    vram_total_bytes: int | None = None

    @property
    def available(self) -> bool:
        return self.temperature_c is not None

    @property
    def temperature_level(self) -> str | None:
        return temperature_level(self.temperature_c, GPU_TEMP_RANGES)

    @property
    def fan_spinning(self) -> bool:
        return self.temperature_c is not None and self.temperature_c >= GPU_FAN_START_TEMP


@dataclass(frozen=True)
class SystemStats:
    cpu: CpuStats = field(default_factory=CpuStats)
    memory: MemoryStats = field(default_factory=MemoryStats)
    gpu: GpuStats = field(default_factory=GpuStats)


@dataclass
class _SensorPaths:
    """Resolved hwmon files, cached so each sample is a handful of small reads."""

    cpu_temperature: Path | None = None
    gpu_edge: Path | None = None
    gpu_junction: Path | None = None
    gpu_memory: Path | None = None
    gpu_vram_used: Path | None = None
    gpu_vram_total: Path | None = None
    gpu_name: str | None = None

    @property
    def complete(self) -> bool:
        return self.cpu_temperature is not None and self.gpu_edge is not None


class SystemStatsService:
    """Single source of truth for CPU, memory, and GPU readings.

    New metrics (GPU usage, frequencies, storage, battery) can be added by
    extending the dataclasses and the private ``_read_*`` helpers without
    touching the widget.
    """

    def __init__(self) -> None:
        self._sensors = _SensorPaths()
        self._last_discovery_monotonic = 0.0
        self._cpu_sample: tuple[int, int] | None = None
        self._last_cpu_usage: float | None = None

        self._discover_sensors()
        self._cpu_sample = self._read_cpu_sample()

    def read(self) -> SystemStats:
        """Return a fresh snapshot, degrading to ``None`` fields when a sensor fails."""
        self._maybe_rediscover_sensors()
        return SystemStats(
            cpu=self._read_cpu(),
            memory=self._read_memory(),
            gpu=self._read_gpu(),
        )

    @property
    def gpu_name(self) -> str | None:
        return self._sensors.gpu_name

    def _maybe_rediscover_sensors(self) -> None:
        if self._sensors.complete:
            return
        now = time.monotonic()
        if now - self._last_discovery_monotonic < SENSOR_REDISCOVERY_INTERVAL_SEC:
            return
        self._discover_sensors()

    def _discover_sensors(self) -> None:
        self._last_discovery_monotonic = time.monotonic()
        sensors = _SensorPaths(gpu_name=self._sensors.gpu_name)

        for hwmon in self._hwmon_devices():
            name = _read_text(hwmon / "name")
            if name is None:
                continue
            inputs = _temperature_inputs(hwmon)
            if not inputs:
                continue

            if sensors.cpu_temperature is None and name in CPU_HWMON_NAMES:
                sensors.cpu_temperature = _pick_input(
                    inputs, CPU_TEMP_LABEL_PRIORITY
                )
            elif sensors.gpu_edge is None and name in GPU_HWMON_NAMES:
                sensors.gpu_edge = _pick_input(inputs, GPU_EDGE_LABELS)
                sensors.gpu_junction = _pick_input(inputs, GPU_JUNCTION_LABELS)
                sensors.gpu_memory = _pick_input(inputs, GPU_MEMORY_LABELS)
                (
                    sensors.gpu_vram_used,
                    sensors.gpu_vram_total,
                ) = _find_amdgpu_vram_paths()
                if sensors.gpu_edge is not None and sensors.gpu_name is None:
                    sensors.gpu_name = _resolve_pci_device_name(hwmon / "device")

        self._sensors = sensors

    @staticmethod
    def _hwmon_devices() -> tuple[Path, ...]:
        try:
            return tuple(sorted(HWMON_ROOT.iterdir()))
        except OSError:
            return ()

    def _read_cpu(self) -> CpuStats:
        sample = self._read_cpu_sample()
        if sample is not None and self._cpu_sample is not None:
            total_delta = sample[0] - self._cpu_sample[0]
            idle_delta = sample[1] - self._cpu_sample[1]
            if total_delta > 0:
                busy_ratio = (total_delta - idle_delta) / total_delta
                self._last_cpu_usage = max(0.0, min(100.0, busy_ratio * 100.0))
        if sample is not None:
            self._cpu_sample = sample

        return CpuStats(
            usage_percent=self._last_cpu_usage,
            temperature_c=_read_temperature(self._sensors.cpu_temperature),
        )

    @staticmethod
    def _read_cpu_sample() -> tuple[int, int] | None:
        """Return ``(total_jiffies, idle_jiffies)`` for the aggregate CPU line."""
        content = _read_text(PROC_STAT)
        if content is None:
            return None
        fields = content.split("\n", 1)[0].split()
        if len(fields) < 6 or fields[0] != "cpu":
            return None
        try:
            values = [int(value) for value in fields[1:]]
        except ValueError:
            return None
        return sum(values), values[3] + values[4]

    @staticmethod
    def _read_memory() -> MemoryStats:
        content = _read_text(PROC_MEMINFO)
        if content is None:
            return MemoryStats()

        wanted = {"MemTotal:", "MemAvailable:"}
        values: dict[str, int] = {}
        for line in content.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] in wanted:
                try:
                    values[fields[0]] = int(fields[1]) * 1024
                except ValueError:
                    continue
                if len(values) == len(wanted):
                    break

        total = values.get("MemTotal:")
        available = values.get("MemAvailable:")
        if total is None or available is None:
            return MemoryStats(total_bytes=total)
        return MemoryStats(used_bytes=max(0, total - available), total_bytes=total)

    def _read_gpu(self) -> GpuStats:
        return GpuStats(
            name=self._sensors.gpu_name,
            temperature_c=_read_temperature(self._sensors.gpu_edge),
            hotspot_temperature_c=_read_temperature(self._sensors.gpu_junction),
            memory_temperature_c=_read_temperature(self._sensors.gpu_memory),
            vram_used_bytes=_read_integer(self._sensors.gpu_vram_used),
            vram_total_bytes=_read_integer(self._sensors.gpu_vram_total),
        )


def _read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_temperature(path: Path | None) -> float | None:
    """hwmon reports millidegrees Celsius; implausible values are discarded."""
    raw = _read_text(path)
    if not raw:
        return None
    try:
        celsius = int(raw) / 1000.0
    except ValueError:
        return None
    if not -50.0 < celsius < 150.0:
        return None
    return celsius


def _read_integer(path: Path | None) -> int | None:
    raw = _read_text(path)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _find_amdgpu_vram_paths() -> tuple[Path | None, Path | None]:
    """Find VRAM usage files exposed by the amdgpu DRM device."""
    try:
        cards = sorted(DRM_ROOT.glob("card[0-9]*"))
    except OSError:
        return None, None

    for card in cards:
        device = card / "device"
        try:
            driver_name = (device / "driver").resolve().name
        except OSError:
            continue
        if driver_name != "amdgpu":
            continue

        used = device / "mem_info_vram_used"
        total = device / "mem_info_vram_total"
        if used.exists() or total.exists():
            return (
                used if used.exists() else None,
                total if total.exists() else None,
            )
    return None, None


def _temperature_inputs(hwmon: Path) -> dict[str, Path]:
    """Map each ``tempN_label`` (or file name when unlabeled) to its input path."""
    inputs: dict[str, Path] = {}
    try:
        candidates = sorted(hwmon.glob("temp*_input"))
    except OSError:
        return inputs
    for input_path in candidates:
        label_path = input_path.with_name(input_path.name.replace("_input", "_label"))
        label = _read_text(label_path) or input_path.name
        inputs.setdefault(label, input_path)
        inputs.setdefault(input_path.name, input_path)
    return inputs


def _pick_input(inputs: dict[str, Path], priority: tuple[str, ...]) -> Path | None:
    for label in priority:
        path = inputs.get(label)
        if path is not None:
            return path
    return None


def _resolve_pci_device_name(device_dir: Path) -> str | None:
    """Resolve a marketing name from the PCI ids database, if it is installed."""
    vendor = _normalize_pci_id(_read_text(device_dir / "vendor"))
    device = _normalize_pci_id(_read_text(device_dir / "device"))
    if vendor is None or device is None:
        return None

    for pci_ids in PCI_IDS_FILES:
        name = _lookup_pci_name(pci_ids, vendor, device)
        if name is not None:
            return name
    return None


def _normalize_pci_id(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.lower().removeprefix("0x").rjust(4, "0")


def _lookup_pci_name(pci_ids: Path, vendor: str, device: str) -> str | None:
    try:
        with pci_ids.open(encoding="utf-8", errors="replace") as handle:
            in_vendor_block = False
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                if not line.startswith("\t"):
                    if in_vendor_block:
                        return None
                    in_vendor_block = line.lower().startswith(vendor)
                    continue
                if not in_vendor_block or line.startswith("\t\t"):
                    continue
                entry = line.strip()
                if entry.lower().startswith(device):
                    return _shorten_pci_name(entry[len(device):].strip())
    except OSError:
        return None
    return None


def _shorten_pci_name(name: str) -> str | None:
    """Prefer the bracketed marketing name, e.g. Navi 23 [Radeon RX 6600]."""
    if not name:
        return None
    start = name.find("[")
    end = name.rfind("]")
    if 0 <= start < end:
        return name[start + 1 : end]
    return name
