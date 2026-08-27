"""Resolve PipeWire sink capture targets for the audio visualizer."""

from __future__ import annotations

import subprocess

from ...models import AudioSnapshot, MediaPlayerSnapshot, MediaSnapshot

PWCAT_CAPTURE_PROPERTIES = "{ stream.capture.sink = true stream.monitor = true }"


def sink_node_name(name: str) -> str:
    """Return a PipeWire sink node.name, never a Pulse ``*.monitor`` alias."""
    stripped = name.strip()
    if stripped.endswith(".monitor"):
        return stripped[: -len(".monitor")]
    return stripped


def query_default_sink() -> str:
    """Return the PipeWire node.name of the default output sink."""
    try:
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        sink_name = sink_node_name(result.stdout)
        if sink_name:
            return sink_name
    except Exception:
        pass
    return ""


def query_default_sink_monitor() -> str:
    """Return the default sink node.name used as the visualizer capture target."""
    return query_default_sink()


def _player_tokens(player: MediaPlayerSnapshot) -> tuple[str, ...]:
    tokens: list[str] = []
    if player.identity:
        tokens.append(player.identity.casefold())
    bus_name = player.bus_name
    prefix = "org.mpris.MediaPlayer2."
    if bus_name.startswith(prefix):
        suffix = bus_name[len(prefix) :]
        tokens.append(suffix.casefold())
        tokens.append(suffix.split(".")[0].casefold())
    if player.title:
        tokens.append(player.title.casefold())
    return tuple(dict.fromkeys(token for token in tokens if token))


def _stream_matches_player(
    application_name: str,
    stream_title: str,
    tokens: tuple[str, ...],
) -> bool:
    app = application_name.casefold()
    title = stream_title.casefold()
    for token in tokens:
        if token in app or app in token:
            return True
        if title and token in title:
            return True
    return False


def _sink_name_for_device(audio: AudioSnapshot, sink_id: str) -> str | None:
    for device in audio.output_devices:
        if device.id != sink_id:
            continue
        name = sink_node_name(device.name)
        return name or None
    return None


def resolve_monitor_target(
    media_snapshot: MediaSnapshot,
    audio_snapshot: AudioSnapshot,
) -> str:
    """Pick the PipeWire sink node for the active MPRIS player, or the default sink."""
    player = media_snapshot.active
    if player is None:
        return query_default_sink()

    tokens = _player_tokens(player)
    matches: list[tuple[int, str]] = []

    for workspace in audio_snapshot.workspaces_audio:
        for stream in workspace.streams:
            if stream.stream_kind != "playback":
                continue
            if not _stream_matches_player(stream.application_name, stream.title, tokens):
                continue
            score = 0
            if stream.is_playing:
                score += 10
            if not stream.is_muted:
                score += 5
            sink = _sink_name_for_device(audio_snapshot, stream.device_id)
            if sink is None:
                continue
            matches.append((score, sink))

    if not matches:
        return query_default_sink()

    return max(matches, key=lambda item: item[0])[1]


def pwcat_record_command(
    target: str,
    *,
    rate: int,
    channels: int,
    latency: int,
) -> list[str]:
    """Build ``pw-cat --record`` so it captures sink monitor ports, not a source."""
    command = [
        "pw-cat",
        "--record",
        "--format",
        "s16",
        "--rate",
        str(rate),
        "--channels",
        str(channels),
        "--latency",
        str(latency),
        "--properties",
        PWCAT_CAPTURE_PROPERTIES,
    ]
    sink = sink_node_name(target)
    if sink:
        command.extend(["--target", sink])
    command.append("-")
    return command
