"""Audio service providing single source of truth for workspace audio streams."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import replace
from typing import Any, Callable

from ...config import AUDIO_POLL_INTERVAL_SEC
from ...eventbus import EventBus
from ...models import (
    AudioDevice,
    AudioSnapshot,
    AudioStream,
    HyprlandSnapshot,
    SystemVolumeState,
    Window,
    WorkspaceAudioState,
)

AUDIO_CHANGED = "audio_changed"
WORKSPACE_CHANGED = "workspace_changed"
WINDOW_OPENED = "window_opened"
WINDOW_CLOSED = "window_closed"
_AUDIO_COMMAND_TIMEOUT_SEC = 2.0


def friendly_device_description(item: dict[str, Any], *, fallback: str = "") -> str:
    """Pick a human-readable sink/source label from pactl JSON."""
    props = item.get("properties", {}) or {}
    candidates = (
        props.get("node.nick"),
        item.get("description"),
        props.get("node.description"),
        props.get("device.description"),
        item.get("name"),
        fallback,
    )
    for value in candidates:
        text = str(value or "").strip()
        if text and text != "(null)":
            # Prefer a slightly richer label when nick is bare chip name.
            if value == props.get("node.nick"):
                profile = str(props.get("device.profile.description") or "").strip()
                if profile and profile != "(null)" and profile.casefold() not in text.casefold():
                    return f"{text} {profile}"
            return text
    return fallback or "Salida de audio"


def build_system_volume_state(
    sinks: list[dict[str, Any]],
    default_sink_name: str,
) -> SystemVolumeState | None:
    """Resolve default-sink volume/mute from pactl sink list JSON."""
    if not default_sink_name:
        return None
    target = default_sink_name.strip()
    selected: dict[str, Any] | None = None
    for item in sinks:
        name = str(item.get("name", "")).strip()
        if name == target:
            selected = item
            break
    if selected is None and sinks:
        selected = sinks[0]
    if selected is None:
        return None
    name = str(selected.get("name", target)).strip() or target
    return SystemVolumeState(
        sink_name=name,
        sink_description=friendly_device_description(selected, fallback=name),
        volume=AudioService._volume_from_item(selected),
        is_muted=bool(selected.get("mute", False)),
    )


class AudioService:
    """Monitors audio streams and maps them to workspaces using EventBus snapshot events."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._snapshot = AudioSnapshot(workspaces_audio=())
        self._snapshot_lock = threading.RLock()
        self._refresh_lock = threading.RLock()
        self._hyprland_windows: tuple[Window, ...] = ()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscribe_thread: threading.Thread | None = None
        self._subscribe_process: subprocess.Popen[str] | None = None
        self._operation_threads: set[threading.Thread] = set()
        self._operation_threads_lock = threading.Lock()

        self._event_bus.subscribe(WORKSPACE_CHANGED, self._on_hyprland_workspace_changed, on_main=False)
        self._event_bus.subscribe(WINDOW_OPENED, self._on_hyprland_workspace_changed, on_main=False)
        self._event_bus.subscribe(WINDOW_CLOSED, self._on_hyprland_workspace_changed, on_main=False)

    @property
    def snapshot(self) -> AudioSnapshot:
        with self._snapshot_lock:
            return self._snapshot

    def refresh(self) -> None:
        """Synchronously rebuild the audio snapshot from current sink inputs."""
        self._refresh_audio()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="audio-service-monitor",
            daemon=True,
        )
        self._thread.start()
        self._subscribe_thread = threading.Thread(
            target=self._subscribe_loop,
            name="audio-service-subscribe",
            daemon=True,
        )
        self._subscribe_thread.start()

    def close(self) -> None:
        self._stop_event.set()
        process = self._subscribe_process
        self._subscribe_process = None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        for thread in (self._thread, self._subscribe_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._thread = None
        self._subscribe_thread = None
        with self._operation_threads_lock:
            operation_threads = tuple(self._operation_threads)
        for thread in operation_threads:
            if thread is not threading.current_thread():
                thread.join(timeout=_AUDIO_COMMAND_TIMEOUT_SEC + 0.5)
        self._event_bus.unsubscribe(WORKSPACE_CHANGED, self._on_hyprland_workspace_changed)
        self._event_bus.unsubscribe(WINDOW_OPENED, self._on_hyprland_workspace_changed)
        self._event_bus.unsubscribe(WINDOW_CLOSED, self._on_hyprland_workspace_changed)

    def set_stream_volume(self, stream_id: str, volume: float) -> None:
        """Set volume (0.0 to 1.0) for a playback or capture stream."""
        clamped_volume = max(0.0, min(1.0, volume))
        vol_pct = f"{int(clamped_volume * 100)}%"
        stream_kind = self._stream_kind_for_id(stream_id)

        def _exec_set() -> None:
            if stream_kind == "capture":
                command = ["pactl", "set-source-input-volume", str(stream_id), vol_pct]
            else:
                command = ["pactl", "set-sink-input-volume", str(stream_id), vol_pct]
            succeeded = self._run_command(command)
            if not succeeded and stream_kind != "capture":
                succeeded = self._run_command(
                    ["wpctl", "set-volume", str(stream_id), str(clamped_volume)],
                )
            if succeeded and not self._stop_event.is_set():
                self._update_stream_volume_local(stream_id, clamped_volume)

        self._start_operation("audio-set-volume", _exec_set)

    def toggle_stream_mute(self, stream_id: str) -> None:
        """Toggle mute state for a playback or capture stream."""
        stream_kind = self._stream_kind_for_id(stream_id)

        def _exec_toggle() -> None:
            if stream_kind == "capture":
                command = ["pactl", "set-source-input-mute", str(stream_id), "toggle"]
            else:
                command = ["pactl", "set-sink-input-mute", str(stream_id), "toggle"]
            if self._run_command(command) and not self._stop_event.is_set():
                self._toggle_stream_mute_local(stream_id)

        self._start_operation("audio-toggle-mute", _exec_toggle)

    def set_playback_sink(self, stream_id: str, sink_id: str) -> None:
        """Route a playback stream to a different output device."""
        def _exec_move() -> None:
            if self._run_command(["pactl", "move-sink-input", str(stream_id), str(sink_id)]):
                self.refresh()

        self._start_operation("audio-move-playback", _exec_move)

    def set_capture_source(self, stream_id: str, source_id: str) -> None:
        """Route a capture stream to a different input device."""
        def _exec_move() -> None:
            if self._run_command(["pactl", "move-source-input", str(stream_id), str(source_id)]):
                self.refresh()

        self._start_operation("audio-move-capture", _exec_move)

    def _stream_kind_for_id(self, stream_id: str) -> str:
        with self._snapshot_lock:
            for ws_state in self._snapshot.workspaces_audio:
                for stream in ws_state.streams:
                    if stream.id == stream_id:
                        return stream.stream_kind
        return "playback"

    def _on_hyprland_workspace_changed(self, payload: Any) -> None:
        if isinstance(payload, HyprlandSnapshot):
            windows: list[Window] = []
            for ws in payload.workspaces:
                windows.extend(ws.windows)
            self._hyprland_windows = tuple(windows)
            self._refresh_audio()

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._refresh_audio()
            except Exception as err:
                print(f"shell (audio service): {err}")
            self._stop_event.wait(AUDIO_POLL_INTERVAL_SEC)

    def _subscribe_loop(self) -> None:
        """Wake a refresh when Pulse/PipeWire reports sink or server changes."""
        while not self._stop_event.is_set():
            try:
                process = subprocess.Popen(
                    ["pactl", "subscribe"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except OSError:
                return
            self._subscribe_process = process
            stdout = process.stdout
            if stdout is None:
                return
            try:
                for line in stdout:
                    if self._stop_event.is_set():
                        break
                    lowered = line.casefold()
                    if "on sink" in lowered or "on server" in lowered:
                        try:
                            self._refresh_audio()
                        except Exception as err:
                            print(f"shell (audio service subscribe): {err}")
            finally:
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except Exception:
                    pass
                if self._subscribe_process is process:
                    self._subscribe_process = None
            if self._stop_event.is_set():
                break
            self._stop_event.wait(1.0)

    def _refresh_audio(self) -> None:
        with self._refresh_lock:
            self._refresh_audio_locked()

    def _refresh_audio_locked(self) -> None:
        sinks_raw = self._query_pactl_json("sinks")
        sources_raw = self._query_pactl_json("sources")
        output_devices = self._devices_from_sinks(sinks_raw)
        input_devices = self._devices_from_sources(sources_raw)
        output_by_id = {device.id: device for device in output_devices}
        input_by_id = {device.id: device for device in input_devices}

        playback_raw = self._query_pactl_json("sink-inputs")
        capture_raw = self._query_pactl_json("source-inputs")
        mapped_states = self._build_workspace_states(
            playback_raw,
            capture_raw,
            output_by_id,
            input_by_id,
        )
        system_volume = build_system_volume_state(
            sinks_raw,
            self._query_default_sink_name(),
        )
        new_snapshot = AudioSnapshot(
            workspaces_audio=tuple(mapped_states.values()),
            output_devices=tuple(output_devices),
            input_devices=tuple(input_devices),
            system_volume=system_volume,
        )

        with self._snapshot_lock:
            changed = self._snapshot != new_snapshot
            had_audio = any(state.has_audio for state in self._snapshot.workspaces_audio)
            self._snapshot = new_snapshot
            has_audio = any(state.has_audio for state in new_snapshot.workspaces_audio)

        if changed or (has_audio and not had_audio):
            self._event_bus.emit(AUDIO_CHANGED, self.snapshot)

    def _query_default_sink_name(self) -> str:
        try:
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _query_pactl_json(self, target: str) -> list[dict[str, Any]]:
        try:
            res = subprocess.run(
                ["pactl", "-f", "json", "list", target],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, list):
                    return data
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []
        return []

    @staticmethod
    def _run_command(command: list[str]) -> bool:
        """Execute an audio control command without allowing it to block forever."""
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_AUDIO_COMMAND_TIMEOUT_SEC,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"shell (audio service): command unavailable: {error}")
            return False
        if result.returncode != 0:
            print(f"shell (audio service): command failed ({result.returncode}): {' '.join(command[:2])}")
            return False
        return True

    def _start_operation(self, name: str, operation: Callable[[], None]) -> None:
        """Track short-lived control threads so shutdown can wait for them."""
        if self._stop_event.is_set():
            return

        def run() -> None:
            try:
                operation()
            finally:
                with self._operation_threads_lock:
                    self._operation_threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, name=name, daemon=True)
        with self._operation_threads_lock:
            self._operation_threads.add(thread)
        thread.start()

    def _query_output_devices(self) -> list[AudioDevice]:
        return self._devices_from_sinks(self._query_pactl_json("sinks"))

    def _query_input_devices(self) -> list[AudioDevice]:
        return self._devices_from_sources(self._query_pactl_json("sources"))

    def _devices_from_sinks(self, sinks: list[dict[str, Any]]) -> list[AudioDevice]:
        devices: list[AudioDevice] = []
        for item in sinks:
            device_id = str(item.get("index", item.get("id", "")))
            if not device_id:
                continue
            name = str(item.get("name", device_id))
            devices.append(
                AudioDevice(
                    id=device_id,
                    name=name,
                    description=friendly_device_description(item, fallback=name),
                    kind="output",
                ),
            )
        return devices

    def _devices_from_sources(self, sources: list[dict[str, Any]]) -> list[AudioDevice]:
        devices: list[AudioDevice] = []
        for item in sources:
            name = str(item.get("name", ""))
            if name.endswith(".monitor"):
                continue
            device_id = str(item.get("index", item.get("id", "")))
            if not device_id:
                continue
            devices.append(
                AudioDevice(
                    id=device_id,
                    name=name or device_id,
                    description=friendly_device_description(item, fallback=name or device_id),
                    kind="input",
                ),
            )
        return devices

    def _build_workspace_states(
        self,
        playback_raw: list[dict[str, Any]],
        capture_raw: list[dict[str, Any]],
        output_by_id: dict[str, AudioDevice],
        input_by_id: dict[str, AudioDevice],
    ) -> dict[int, WorkspaceAudioState]:
        by_workspace: dict[int, list[AudioStream]] = {}

        for item in playback_raw:
            stream = self._parse_stream_item(
                item,
                stream_kind="playback",
                device_by_id=output_by_id,
                device_key="sink",
            )
            if stream is None:
                continue
            by_workspace.setdefault(stream.workspace_id, []).append(stream)

        for item in capture_raw:
            stream = self._parse_stream_item(
                item,
                stream_kind="capture",
                device_by_id=input_by_id,
                device_key="source",
            )
            if stream is None:
                continue
            by_workspace.setdefault(stream.workspace_id, []).append(stream)

        workspace_states: dict[int, WorkspaceAudioState] = {}
        for ws_id, streams in by_workspace.items():
            has_audio = len(streams) > 0
            is_playing = any(
                stream.is_playing for stream in streams if stream.stream_kind == "playback"
            )
            has_muted = any(stream.is_muted for stream in streams)
            workspace_states[ws_id] = WorkspaceAudioState(
                workspace_id=ws_id,
                has_audio=has_audio,
                is_playing=is_playing,
                has_muted=has_muted,
                streams=tuple(streams),
            )

        return workspace_states

    def _parse_stream_item(
        self,
        item: dict[str, Any],
        *,
        stream_kind: str,
        device_by_id: dict[str, AudioDevice],
        device_key: str,
    ) -> AudioStream | None:
        stream_id = str(item.get("index", item.get("id", "")))
        if not stream_id:
            return None

        props = item.get("properties", {})
        app_name = (
            props.get("application.name")
            or props.get("node.name")
            or item.get("name", "Audio")
        )
        media_title = (
            props.get("media.name")
            or props.get("media.title")
            or item.get("media_name", app_name)
        )
        app_pid = props.get("application.process.id")
        corked = item.get("corked", False)
        is_muted = bool(item.get("mute", False))
        vol_val = self._volume_from_item(item)

        device_id = str(item.get(device_key, ""))
        device = device_by_id.get(device_id)
        device_name = device.description if device is not None else device_id or "Desconocido"

        pid = None
        if app_pid is not None:
            try:
                pid = int(app_pid)
            except (TypeError, ValueError):
                pid = None

        candidates: list[Window] = []
        if pid is not None:
            candidates = [win for win in self._hyprland_windows if win.pid == pid]

        if not candidates:
            candidates = [
                win
                for win in self._hyprland_windows
                if (
                    app_name.lower() in win.app_class.lower()
                    or win.app_class.lower() in app_name.lower()
                    or app_name.lower() in win.application_name.lower()
                    or win.application_name.lower() in app_name.lower()
                )
            ]

        if not candidates:
            return None

        selected = self._select_best_window_candidate(candidates, media_title, app_name)
        icon_name = selected.icon or (
            "audio-input-microphone" if stream_kind == "capture" else "audio-volume-high"
        )

        return AudioStream(
            id=stream_id,
            application_name=str(app_name),
            title=str(media_title),
            icon=icon_name,
            volume=vol_val,
            is_muted=is_muted,
            is_playing=not corked if stream_kind == "playback" else True,
            stream_kind=stream_kind,
            device_id=device_id,
            device_name=device_name,
            workspace_id=selected.workspace_id,
            window_address=selected.address,
        )

    @staticmethod
    def _volume_from_item(item: dict[str, Any]) -> float:
        volume_obj = item.get("volume", {})
        if isinstance(volume_obj, dict):
            for channel_data in volume_obj.values():
                if isinstance(channel_data, dict) and "value_percent" in channel_data:
                    raw_pct = str(channel_data["value_percent"]).rstrip("%")
                    try:
                        return float(raw_pct) / 100.0
                    except ValueError:
                        pass
        return 1.0

    def _select_best_window_candidate(
        self,
        candidates: list[Window],
        media_title: str,
        app_name: str,
    ) -> Window:
        if len(candidates) == 1:
            return candidates[0]

        normalized_title = media_title.casefold()
        normalized_app = app_name.casefold()

        def score(win: Window) -> int:
            score_value = 0
            if win.title and normalized_title and normalized_title in win.title.casefold():
                score_value += 20
            if win.title and normalized_app and normalized_app in win.title.casefold():
                score_value += 10
            if win.application_name and normalized_app and normalized_app in win.application_name.casefold():
                score_value += 5
            if win.workspace_id < 0:
                score_value += 1
            return score_value

        return max(candidates, key=score)

    def _map_streams(
        self,
        mapper: Callable[[AudioStream], AudioStream],
    ) -> None:
        with self._snapshot_lock:
            new_states: list[WorkspaceAudioState] = []
            for ws_state in self._snapshot.workspaces_audio:
                new_streams = tuple(mapper(stream) for stream in ws_state.streams)
                new_states.append(
                    WorkspaceAudioState(
                        workspace_id=ws_state.workspace_id,
                        has_audio=len(new_streams) > 0,
                        is_playing=any(
                            stream.is_playing
                            for stream in new_streams
                            if stream.stream_kind == "playback"
                        ),
                        has_muted=any(stream.is_muted for stream in new_streams),
                        streams=new_streams,
                    ),
                )
            self._snapshot = replace(self._snapshot, workspaces_audio=tuple(new_states))
        self._event_bus.emit(AUDIO_CHANGED, self.snapshot)

    def _update_stream_volume_local(self, stream_id: str, new_volume: float) -> None:
        self._map_streams(
            lambda stream: (
                replace(stream, volume=new_volume)
                if stream.id == stream_id
                else stream
            ),
        )

    def _toggle_stream_mute_local(self, stream_id: str) -> None:
        self._map_streams(
            lambda stream: (
                replace(stream, is_muted=not stream.is_muted)
                if stream.id == stream_id
                else stream
            ),
        )
