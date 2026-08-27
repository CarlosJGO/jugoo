"""Compact CPU, memory, and GPU monitor rendered from SystemStatsService data."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ...config import (
    STATS_CPU_BAR_TEMP_MAX_C,
    STATS_CPU_BAR_TEMP_MIN_C,
    STATS_CPU_BAR_WIDTH,
    STATS_GPU_FAN_ICON_SIZE,
    STATS_SECTION_SPACING,
    SYSTEM_STATS_UPDATE_INTERVAL,
)
from ...servicios.sistema.system import (
    TEMPERATURE_COLD,
    TEMPERATURE_HOT,
    TEMPERATURE_NORMAL,
    TEMPERATURE_WARM,
    SystemStats,
    SystemStatsService,
)
from ...ui import SHELL_MODULE_STACK_SPACING, ShellModule, shell_label

TEMPERATURE_CSS_CLASSES = {
    TEMPERATURE_COLD: "shell-temp-cold",
    TEMPERATURE_NORMAL: "shell-temp-normal",
    TEMPERATURE_WARM: "shell-temp-warm",
    TEMPERATURE_HOT: "shell-temp-hot",
}

FULL_TURN = 2 * math.pi
FAN_BLADE_COUNT = 6
FAN_HUB_RADIUS_RATIO = 0.30
FAN_HUB_HOLE_RATIO = 0.11
FAN_REVOLUTIONS_PER_SECOND = 0.5
FAN_FRAME_INTERVAL_US = 1_000_000 // 24
BYTES_PER_GIB = 1024**3
# Keeps the thermal color perceivable when the reading sits at the bar's floor.
MIN_VISIBLE_FILL_PX = 6
UNKNOWN_TEMPERATURE = "-- °C"
UNKNOWN_VALUE = "--"


class FanIcon(Gtk.DrawingArea):
    """Cairo-drawn fan whose color comes from CSS and whose spin is frame-clock driven."""

    def __init__(self, size: int) -> None:
        super().__init__()
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.get_style_context().add_class("stats-gpu-fan")

        self._angle = 0.0
        self._tick_id: int | None = None
        self._last_frame_time_us: int | None = None
        self._last_draw_time_us = 0

        self.connect("draw", self._on_draw)
        self.connect("destroy", self._on_destroy)

    @property
    def spinning(self) -> bool:
        return self._tick_id is not None

    def set_spinning(self, spinning: bool) -> None:
        """Start or stop the rotation; a stopped fan consumes no frame callbacks."""
        if spinning == self.spinning:
            return
        if spinning:
            self._last_frame_time_us = None
            self._tick_id = self.add_tick_callback(self._on_tick)
            return
        self.remove_tick_callback(self._tick_id)
        self._tick_id = None
        self.queue_draw()

    def _on_tick(self, _widget: Gtk.Widget, frame_clock: object) -> bool:
        frame_time_us = frame_clock.get_frame_time()
        if self._last_frame_time_us is not None:
            elapsed_sec = (frame_time_us - self._last_frame_time_us) / 1_000_000
            self._angle = (
                self._angle + elapsed_sec * FAN_REVOLUTIONS_PER_SECOND * FULL_TURN
            ) % FULL_TURN
        self._last_frame_time_us = frame_time_us

        if frame_time_us - self._last_draw_time_us >= FAN_FRAME_INTERVAL_US:
            self._last_draw_time_us = frame_time_us
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _on_draw(self, _widget: Gtk.Widget, context: object) -> bool:
        allocation = self.get_allocation()
        radius = min(allocation.width, allocation.height) / 2 - 1
        if radius <= 0:
            return True

        style = self.get_style_context()
        color = style.get_color(style.get_state())

        # Blades are cut out of a disc, so the shape is composed in an isolated
        # group: clearing straight onto the bar surface would punch holes
        # through the module's translucent background.
        context.save()
        context.push_group()
        context.save()
        context.translate(allocation.width / 2, allocation.height / 2)
        context.rotate(self._angle)
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.arc(0, 0, radius, 0, FULL_TURN)
        context.fill()

        context.set_operator(cairo.Operator.CLEAR)
        for blade in range(FAN_BLADE_COUNT):
            context.save()
            context.rotate(blade * FULL_TURN / FAN_BLADE_COUNT)
            context.move_to(radius * 0.18, -radius * 0.02)
            context.curve_to(
                radius * 0.60, -radius * 0.10,
                radius * 0.95, -radius * 0.35,
                radius * 1.25, -radius * 0.75,
            )
            context.curve_to(
                radius * 1.10, -radius * 0.15,
                radius * 0.80, radius * 0.22,
                radius * 0.24, radius * 0.26,
            )
            context.close_path()
            context.fill()
            context.restore()

        context.set_operator(cairo.Operator.OVER)
        context.arc(0, 0, radius * FAN_HUB_RADIUS_RATIO, 0, FULL_TURN)
        context.fill()
        context.set_operator(cairo.Operator.CLEAR)
        context.arc(0, 0, radius * FAN_HUB_HOLE_RATIO, 0, FULL_TURN)
        context.fill()
        context.restore()

        context.pop_group_to_source()
        context.set_operator(cairo.Operator.OVER)
        context.paint()
        context.restore()
        return True

    def _on_destroy(self, *_args) -> None:
        if self._tick_id is not None:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = None


class StatsWidget(ShellModule):
    """Renders the periodic system snapshot; owns no sensor access of its own."""

    def __init__(self, stats_service: SystemStatsService) -> None:
        super().__init__(
            "stats-widget",
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=STATS_SECTION_SPACING,
        )
        self._stats_service = stats_service
        self._timer_id: int | None = None

        self.pack_start(self._build_cpu_section(), False, False, 0)
        self.pack_start(self._build_memory_section(), False, False, 0)
        self.pack_start(self._build_gpu_section(), False, False, 0)

        self.connect("destroy", self._on_destroy)
        self._refresh()
        self._timer_id = GLib.timeout_add_seconds(
            SYSTEM_STATS_UPDATE_INTERVAL,
            self._on_timer,
        )

    def _build_cpu_section(self) -> Gtk.Box:
        section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SHELL_MODULE_STACK_SPACING,
        )
        section.get_style_context().add_class("stats-cpu")

        self._cpu_usage_label = shell_label(
            "",
            role="caption",
            css_classes=("stats-cpu-label",),
            xalign=0.5,
        )
        section.pack_start(self._cpu_usage_label, False, False, 0)

        self._cpu_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._cpu_bar.get_style_context().add_class("stats-cpu-bar")
        self._cpu_bar.set_size_request(STATS_CPU_BAR_WIDTH, -1)

        self._cpu_bar_fill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._cpu_bar_fill.get_style_context().add_class("stats-cpu-fill")
        self._cpu_bar.pack_start(self._cpu_bar_fill, False, False, 0)

        self._cpu_temperature_label = shell_label(
            UNKNOWN_TEMPERATURE,
            role="caption",
            css_classes=("stats-cpu-temperature",),
            xalign=0.5,
        )
        self._cpu_temperature_label.set_halign(Gtk.Align.CENTER)
        self._cpu_temperature_label.set_valign(Gtk.Align.CENTER)

        overlay = Gtk.Overlay()
        overlay.add(self._cpu_bar)
        overlay.add_overlay(self._cpu_temperature_label)
        section.pack_start(overlay, False, False, 0)
        return section

    def _build_memory_section(self) -> Gtk.Box:
        section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SHELL_MODULE_STACK_SPACING,
        )
        section.get_style_context().add_class("stats-memory")

        section.pack_start(
            shell_label(
                "RAM",
                role="muted",
                css_classes=("stats-memory-label",),
                xalign=0.0,
            ),
            False,
            False,
            0,
        )
        self._memory_value_label = shell_label(
            "",
            role="caption",
            css_classes=("stats-memory-value",),
            xalign=0.0,
        )
        section.pack_start(self._memory_value_label, False, False, 0)
        return section

    def _build_gpu_section(self) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        section.get_style_context().add_class("stats-gpu")
        self._gpu_fan = FanIcon(STATS_GPU_FAN_ICON_SIZE)
        section.pack_start(self._gpu_fan, False, False, 0)
        return section

    def _on_timer(self) -> bool:
        self._refresh()
        return GLib.SOURCE_CONTINUE

    def _refresh(self) -> None:
        stats = self._stats_service.read()
        self._update_cpu(stats)
        self._update_memory(stats)
        self._update_gpu(stats)

    def _update_cpu(self, stats: SystemStats) -> None:
        usage = stats.cpu.usage_percent
        self._cpu_usage_label.set_text(
            f"CPU {usage:.0f}%" if usage is not None else f"CPU {UNKNOWN_VALUE}%"
        )

        temperature = stats.cpu.temperature_c
        self._cpu_temperature_label.set_text(
            f"{temperature:.0f} °C" if temperature is not None else UNKNOWN_TEMPERATURE
        )
        self._cpu_bar_fill.set_size_request(
            self._temperature_bar_width(temperature), -1
        )
        _apply_temperature_class(self._cpu_bar_fill, stats.cpu.temperature_level)

    def _update_memory(self, stats: SystemStats) -> None:
        used = stats.memory.used_bytes
        total = stats.memory.total_bytes
        if used is None or not total:
            self._memory_value_label.set_text(UNKNOWN_VALUE)
            return
        self._memory_value_label.set_text(
            f"{used / BYTES_PER_GIB:.1f} / {total / BYTES_PER_GIB:.1f} GB"
        )

    def _update_gpu(self, stats: SystemStats) -> None:
        gpu = stats.gpu
        _apply_temperature_class(self._gpu_fan, gpu.temperature_level)
        self._gpu_fan.set_spinning(gpu.fan_spinning)

        style = self._gpu_fan.get_style_context()
        if gpu.fan_spinning:
            style.add_class("stats-gpu-fan-spinning")
        else:
            style.remove_class("stats-gpu-fan-spinning")

        self._gpu_fan.set_tooltip_text(self._gpu_tooltip(stats))

    @staticmethod
    def _gpu_tooltip(stats: SystemStats) -> str:
        gpu = stats.gpu
        name = gpu.name or "GPU"

        if not gpu.available:
            return f"{name}: sin datos"

        details = [
            f"{name}",
            f"Temperatura: {gpu.temperature_c:.0f} °C",
        ]

        if gpu.hotspot_temperature_c is not None:
            details.append(
                f"Hot spot: {gpu.hotspot_temperature_c:.0f} °C"
            )

        if gpu.vram_used_bytes is not None:
            used_gb = gpu.vram_used_bytes / BYTES_PER_GIB
            if gpu.vram_total_bytes is not None:
                total_gb = gpu.vram_total_bytes / BYTES_PER_GIB
                details.append(f"VRAM: {used_gb:.1f} / {total_gb:.1f} GB")
            else:
                details.append(f"VRAM: {used_gb:.1f} GB")

        return "\n".join(details)

    @staticmethod
    def _temperature_bar_width(temperature_c: float | None) -> int:
        if temperature_c is None:
            return 0
        span = STATS_CPU_BAR_TEMP_MAX_C - STATS_CPU_BAR_TEMP_MIN_C
        ratio = (temperature_c - STATS_CPU_BAR_TEMP_MIN_C) / span
        width = int(round(STATS_CPU_BAR_WIDTH * max(0.0, min(1.0, ratio))))
        return max(MIN_VISIBLE_FILL_PX, width)

    def _on_destroy(self, *_args) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None


def _apply_temperature_class(widget: Gtk.Widget, level: str | None) -> None:
    style = widget.get_style_context()
    for state, css_class in TEMPERATURE_CSS_CLASSES.items():
        if state == level:
            style.add_class(css_class)
        else:
            style.remove_class(css_class)
