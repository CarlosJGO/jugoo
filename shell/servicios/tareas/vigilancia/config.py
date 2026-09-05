"""Editable watcher thresholds, sourced from shell.config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .... import config as shell_config


@dataclass(frozen=True)
class WatcherConfig:
    enabled: bool = True
    poll_interval_sec: int = 45
    distraction_threshold_sec: int = 20 * 60
    urgent_window_sec: int = 2 * 60 * 60
    future_horizon_sec: int = 8 * 60 * 60
    reminder_cooldown_sec: int = 60 * 60
    snooze_short_sec: int = 15 * 60
    snooze_long_sec: int = 60 * 60
    max_reminders_per_occurrence: int = 4
    notification_timeout_ms: int = 12000
    ai_enabled: bool = True
    ai_binary: str = "llama-cli"
    ai_model_path: str = "~/IA/models/llama-3.1-8b-instruct-q6_k.gguf"
    ai_context_size: int = 512
    ai_max_tokens: int = 32
    ai_timeout_sec: int = 25
    ai_ngl: int = 99
    ai_batch_size: int = 64
    ai_threads: int = 4
    ai_layer_count: int = 32
    resource_monitor_enabled: bool = True
    minimum_vram_margin_bytes: int = 384 * 1024 * 1024
    maximum_gpu_usage_percent: float = 80.0
    minimum_available_ram_bytes: int = 1536 * 1024 * 1024
    compute_overhead_bytes: int = 384 * 1024 * 1024

    @classmethod
    def from_shell(cls) -> WatcherConfig:
        return cls(
            enabled=bool(shell_config.TASK_WATCHER_ENABLED),
            poll_interval_sec=int(shell_config.TASK_WATCHER_POLL_INTERVAL_SEC),
            distraction_threshold_sec=int(shell_config.TASK_WATCHER_DISTRACTION_THRESHOLD_SEC),
            urgent_window_sec=int(shell_config.TASK_WATCHER_URGENT_WINDOW_SEC),
            future_horizon_sec=int(shell_config.TASK_WATCHER_FUTURE_HORIZON_SEC),
            reminder_cooldown_sec=int(shell_config.TASK_WATCHER_REMINDER_COOLDOWN_SEC),
            snooze_short_sec=int(shell_config.TASK_WATCHER_SNOOZE_SHORT_SEC),
            snooze_long_sec=int(shell_config.TASK_WATCHER_SNOOZE_LONG_SEC),
            max_reminders_per_occurrence=int(
                shell_config.TASK_WATCHER_MAX_REMINDERS_PER_OCCURRENCE
            ),
            notification_timeout_ms=int(shell_config.TASK_WATCHER_NOTIFICATION_TIMEOUT_MS),
            ai_enabled=bool(shell_config.TASK_WATCHER_AI_ENABLED),
            ai_binary=str(shell_config.TASK_WATCHER_AI_BINARY),
            ai_model_path=str(shell_config.TASK_WATCHER_AI_MODEL_PATH),
            ai_context_size=int(shell_config.TASK_WATCHER_AI_CONTEXT_SIZE),
            ai_max_tokens=int(shell_config.TASK_WATCHER_AI_MAX_TOKENS),
            ai_timeout_sec=int(shell_config.TASK_WATCHER_AI_TIMEOUT_SEC),
            ai_ngl=int(shell_config.TASK_WATCHER_AI_NGL),
            ai_batch_size=int(shell_config.TASK_WATCHER_AI_BATCH_SIZE),
            ai_threads=int(shell_config.TASK_WATCHER_AI_THREADS),
            ai_layer_count=int(shell_config.TASK_WATCHER_AI_LAYER_COUNT),
            resource_monitor_enabled=bool(shell_config.TASK_WATCHER_RESOURCE_MONITOR_ENABLED),
            minimum_vram_margin_bytes=int(shell_config.TASK_WATCHER_MINIMUM_VRAM_MARGIN_BYTES),
            maximum_gpu_usage_percent=float(shell_config.TASK_WATCHER_MAXIMUM_GPU_USAGE_PERCENT),
            minimum_available_ram_bytes=int(
                shell_config.TASK_WATCHER_MINIMUM_AVAILABLE_RAM_BYTES
            ),
            compute_overhead_bytes=int(shell_config.TASK_WATCHER_COMPUTE_OVERHEAD_BYTES),
        )

    def model_file(self) -> Path:
        return Path(self.ai_model_path).expanduser()
