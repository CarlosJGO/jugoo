"""PipeWire-backed audio level sampler for the active-window visualizer."""

from __future__ import annotations

import logging
import os
import select
import subprocess
import threading
import time

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

from ...config import (
    AUDIO_VISUALIZER_BAR_COUNT,
    AUDIO_VISUALIZER_INTERVAL_MS,
    AUDIO_VISUALIZER_PCM_CHANNELS,
    AUDIO_VISUALIZER_PCM_LATENCY,
    AUDIO_VISUALIZER_PCM_RATE,
)
from ...eventbus import EventBus
from ...models import AudioSnapshot, AudioVisualizerSnapshot, MediaSnapshot
from .audio import AUDIO_CHANGED, AudioService
from .audio_levels import (
    bars_have_energy,
    compute_spectrum_frame,
    empty_bars,
    empty_colors,
    merge_visualizer_snapshot,
    pcm_rms,
    visualizer_should_sample,
)
from ..multimedia.media import MEDIA_CHANGED, MediaService
from .pipewire_monitor import pwcat_record_command, resolve_monitor_target

AUDIO_VISUALIZER_CHANGED = "audio_visualizer_changed"

_logger = logging.getLogger(__name__)
_DEBUG_LOG_INTERVAL_SEC = 2.0
_PCM_BUFFER_BYTES = AUDIO_VISUALIZER_PCM_RATE * 2  # ~1s of s16 mono


class AudioVisualizerService:
    """Samples PipeWire monitor PCM and publishes smoothed bar levels."""

    def __init__(
        self,
        event_bus: EventBus,
        media_service: MediaService,
        audio_service: AudioService,
    ) -> None:
        self._event_bus = event_bus
        self._media_service = media_service
        self._audio_service = audio_service
        self._snapshot = AudioVisualizerSnapshot.hidden(AUDIO_VISUALIZER_BAR_COUNT)
        self._bars = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
        self._peaks = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
        self._colors = empty_colors(AUDIO_VISUALIZER_BAR_COUNT)
        self._pcm_buffer = bytearray()
        self._monitor_target = ""
        self._stop_event = threading.Event()
        self._sampler_enabled = False
        self._sampler_thread: threading.Thread | None = None
        self._pwcat_process: subprocess.Popen[bytes] | None = None
        self._state_lock = threading.RLock()
        self._last_emit_monotonic = 0.0
        self._last_debug_log_monotonic = 0.0

        self._event_bus.subscribe(MEDIA_CHANGED, self._on_media_changed, on_main=False)
        self._event_bus.subscribe(AUDIO_CHANGED, self._on_audio_changed, on_main=False)

    @property
    def snapshot(self) -> AudioVisualizerSnapshot:
        return self._snapshot

    def start(self) -> None:
        self._sync_sampling(force=True)

    def close(self) -> None:
        self._event_bus.unsubscribe(MEDIA_CHANGED, self._on_media_changed)
        self._event_bus.unsubscribe(AUDIO_CHANGED, self._on_audio_changed)
        self._stop_sampling()

    def _on_media_changed(self, _media_snapshot: MediaSnapshot) -> None:
        self._handle_target_or_sampling_changed()

    def _on_audio_changed(self, _audio_snapshot: AudioSnapshot) -> None:
        self._handle_target_or_sampling_changed()

    def _handle_target_or_sampling_changed(self) -> bool:
        self._refresh_monitor_target()
        self._sync_sampling(force=True)
        return False

    def _refresh_monitor_target(self) -> None:
        target = resolve_monitor_target(
            self._media_service.snapshot,
            self._audio_service.snapshot,
        )
        with self._state_lock:
            if target == self._monitor_target:
                return
            self._monitor_target = target
            self._stop_pwcat_locked()
            self._pcm_buffer.clear()
        _logger.debug("Visualizer monitor target -> %s", target)

    def _sync_sampling(self, *, force: bool) -> None:
        should_sample = visualizer_should_sample(self._media_service.snapshot)
        with self._state_lock:
            if should_sample:
                if not self._monitor_target:
                    self._monitor_target = resolve_monitor_target(
                        self._media_service.snapshot,
                        self._audio_service.snapshot,
                    )
                if self._sampler_thread is None or not self._sampler_thread.is_alive():
                    self._stop_event.clear()
                    self._sampler_thread = threading.Thread(
                        target=self._sampler_loop,
                        name="audio-visualizer-sampler",
                        daemon=True,
                    )
                    self._sampler_thread.start()
                self._sampler_enabled = True
            elif self._sampler_enabled or force:
                self._sampler_enabled = False
                self._stop_pwcat_locked()
                self._pcm_buffer.clear()
                if self._sampler_thread is None or not self._sampler_thread.is_alive():
                    if bars_have_energy(self._bars):
                        self._stop_event.clear()
                        self._sampler_thread = threading.Thread(
                            target=self._sampler_loop,
                            name="audio-visualizer-sampler",
                            daemon=True,
                        )
                        self._sampler_thread.start()
                    else:
                        self._bars = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
                        self._peaks = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
                        self._colors = empty_colors(AUDIO_VISUALIZER_BAR_COUNT)
                self._publish_snapshot(force=True)

    def _stop_sampling(self) -> None:
        with self._state_lock:
            self._sampler_enabled = False
            self._stop_event.set()
            self._stop_pwcat_locked()
        thread = self._sampler_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._sampler_thread = None
        self._bars = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
        self._peaks = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
        self._colors = empty_colors(AUDIO_VISUALIZER_BAR_COUNT)
        self._publish_snapshot(force=True)

    def _sampler_loop(self) -> None:
        interval_sec = AUDIO_VISUALIZER_INTERVAL_MS / 1000.0
        while not self._stop_event.is_set():
            with self._state_lock:
                enabled = self._sampler_enabled
            if not enabled:
                time.sleep(interval_sec)
                self._emit_decayed()
                continue

            self._ensure_pwcat_process()
            process = self._pwcat_process
            if process is None or process.stdout is None:
                time.sleep(interval_sec)
                self._emit_decayed()
                continue

            chunk = self._read_pcm_chunk(process)
            if not chunk:
                if process.poll() is not None:
                    self._restart_pwcat_process()
                time.sleep(interval_sec * 0.5)
                self._emit_decayed()
                continue

            with self._state_lock:
                self._pcm_buffer.extend(chunk)
                if len(self._pcm_buffer) > _PCM_BUFFER_BYTES:
                    del self._pcm_buffer[:-_PCM_BUFFER_BYTES]
                pcm = bytes(self._pcm_buffer)
                self._bars, self._peaks, self._colors = compute_spectrum_frame(
                    pcm,
                    bar_count=AUDIO_VISUALIZER_BAR_COUNT,
                    previous_bars=self._bars,
                    previous_peaks=self._peaks,
                    sample_rate=AUDIO_VISUALIZER_PCM_RATE,
                )
                bars = tuple(self._bars)
                target = self._monitor_target

            self._maybe_log_debug(target, chunk, pcm, bars)

            now = time.monotonic()
            if now - self._last_emit_monotonic >= interval_sec:
                self._last_emit_monotonic = now
                self._publish_snapshot(force=False)

            time.sleep(max(0.0, interval_sec * 0.25))

    @staticmethod
    def _read_pcm_chunk(process: subprocess.Popen[bytes], *, max_bytes: int = 4096) -> bytes:
        stdout = process.stdout
        if stdout is None:
            return b""
        try:
            fd = stdout.fileno()
            ready, _, _ = select.select([fd], [], [], 0.08)
            if not ready:
                return b""
            return os.read(fd, max_bytes)
        except Exception as exc:
            _logger.debug("Visualizer PCM read failed: %s", exc)
            return b""

    def _maybe_log_debug(
        self,
        target: str,
        chunk: bytes,
        pcm: bytes,
        bars: tuple[float, ...],
    ) -> None:
        if not _logger.isEnabledFor(logging.DEBUG):
            return
        now = time.monotonic()
        if now - self._last_debug_log_monotonic < _DEBUG_LOG_INTERVAL_SEC:
            return
        self._last_debug_log_monotonic = now
        sample_count = len(pcm) // 2
        _logger.debug(
            "visualizer target=%s chunk_bytes=%d samples=%d chunk_rms=%.1f pcm_rms=%.1f bars=%s",
            target,
            len(chunk),
            sample_count,
            pcm_rms(chunk),
            pcm_rms(pcm),
            tuple(round(value, 2) for value in bars),
        )

    def _emit_decayed(self) -> None:
        with self._state_lock:
            self._bars, self._peaks, self._colors = compute_spectrum_frame(
                b"",
                bar_count=AUDIO_VISUALIZER_BAR_COUNT,
                previous_bars=self._bars,
                previous_peaks=self._peaks,
                sample_rate=AUDIO_VISUALIZER_PCM_RATE,
            )
            exhausted = not self._sampler_enabled and not bars_have_energy(self._bars)
            if exhausted:
                self._bars = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
                self._peaks = empty_bars(AUDIO_VISUALIZER_BAR_COUNT)
                self._colors = empty_colors(AUDIO_VISUALIZER_BAR_COUNT)
                self._stop_event.set()
        now = time.monotonic()
        if now - self._last_emit_monotonic >= AUDIO_VISUALIZER_INTERVAL_MS / 1000.0 or exhausted:
            self._last_emit_monotonic = now
            self._publish_snapshot(force=exhausted)

    def _ensure_pwcat_process(self) -> None:
        if self._pwcat_process is not None and self._pwcat_process.poll() is None:
            return
        self._stop_pwcat_locked()
        target = self._monitor_target or resolve_monitor_target(
            self._media_service.snapshot,
            self._audio_service.snapshot,
        )
        with self._state_lock:
            self._monitor_target = target
        command = pwcat_record_command(
            target,
            rate=AUDIO_VISUALIZER_PCM_RATE,
            channels=AUDIO_VISUALIZER_PCM_CHANNELS,
            latency=AUDIO_VISUALIZER_PCM_LATENCY,
        )
        try:
            self._pwcat_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            _logger.debug("Started pw-cat for visualizer: %s", " ".join(command))
        except OSError as exc:
            _logger.debug("Could not start pw-cat visualizer: %s", exc)
            self._pwcat_process = None

    def _restart_pwcat_process(self) -> None:
        with self._state_lock:
            self._stop_pwcat_locked()
        self._ensure_pwcat_process()

    def _stop_pwcat_locked(self) -> None:
        process = self._pwcat_process
        self._pwcat_process = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=0.2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _publish_snapshot(self, *, force: bool) -> None:
        visible, bars, peaks, colors = merge_visualizer_snapshot(
            self._media_service.snapshot,
            self._bars,
            bar_count=AUDIO_VISUALIZER_BAR_COUNT,
            colors=self._colors,
            peaks=self._peaks,
        )
        next_snapshot = AudioVisualizerSnapshot(
            visible=visible,
            bars=bars,
            peaks=peaks,
            colors=colors,
        )
        if not force and next_snapshot == self._snapshot:
            return
        self._snapshot = next_snapshot
        self._event_bus.emit(AUDIO_VISUALIZER_CHANGED, next_snapshot)
