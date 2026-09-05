"""Decide whether launching llama-cli is reasonably safe right now."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...sistema.system import ComputeResources, read_compute_resources
from .config import WatcherConfig

ResourceReader = Callable[[], ComputeResources]


@dataclass(frozen=True)
class AiViability:
    viable: bool
    reason: str
    estimated_vram_bytes: int | None = None
    available_vram_bytes: int | None = None


def estimate_model_vram_bytes(
    model_path: Path,
    *,
    context_size: int,
    ngl: int,
    layer_count: int,
    overhead_bytes: int,
) -> int | None:
    try:
        file_size = model_path.stat().st_size
    except OSError:
        return None
    layers = max(1, int(layer_count))
    if ngl < 0 or ngl >= layers:
        fraction = 1.0
    else:
        fraction = ngl / layers
    # GGUF size is close to weight memory; a small factor covers extra tensors.
    weights = int(file_size * 1.05 * fraction)
    # Llama-style KV: 2 * layers * kv_heads * head_dim * ctx * 2 bytes (fp16).
    kv = 2 * 32 * 8 * 128 * max(int(context_size), 1) * 2
    return weights + kv + max(0, int(overhead_bytes))


def evaluate_ai_viability(
    resources: ComputeResources,
    *,
    config: WatcherConfig,
    model_path: Path | None = None,
) -> AiViability:
    if not config.ai_enabled:
        return AiViability(False, "ai_disabled")
    if not config.resource_monitor_enabled:
        return AiViability(False, "monitor_disabled")
    path = model_path if model_path is not None else config.model_file()
    if not path.is_file():
        return AiViability(False, "model_missing")
    if not resources.gpu_memory_reliable:
        return AiViability(False, "unreliable")
    estimated = estimate_model_vram_bytes(
        path,
        context_size=config.ai_context_size,
        ngl=config.ai_ngl,
        layer_count=config.ai_layer_count,
        overhead_bytes=config.compute_overhead_bytes,
    )
    if estimated is None:
        return AiViability(False, "unreliable")
    available = resources.vram_available_bytes
    needed = estimated + max(0, config.minimum_vram_margin_bytes)
    if available is None or needed >= available:
        return AiViability(
            False,
            "vram",
            estimated_vram_bytes=estimated,
            available_vram_bytes=available,
        )
    if (
        resources.gpu_usage_percent is not None
        and resources.gpu_usage_percent > config.maximum_gpu_usage_percent
    ):
        return AiViability(
            False,
            "gpu_busy",
            estimated_vram_bytes=estimated,
            available_vram_bytes=available,
        )
    if (
        resources.ram_available_bytes is not None
        and resources.ram_available_bytes < config.minimum_available_ram_bytes
    ):
        return AiViability(
            False,
            "ram",
            estimated_vram_bytes=estimated,
            available_vram_bytes=available,
        )
    return AiViability(
        True,
        "ok",
        estimated_vram_bytes=estimated,
        available_vram_bytes=available,
    )


class ResourceMonitor:
    def __init__(self, reader: ResourceReader | None = None) -> None:
        self._reader = reader or read_compute_resources

    def snapshot(self) -> ComputeResources:
        return self._reader()

    def viability(self, config: WatcherConfig) -> AiViability:
        try:
            resources = self.snapshot()
        except Exception:
            return AiViability(False, "unreliable")
        return evaluate_ai_viability(resources, config=config)
