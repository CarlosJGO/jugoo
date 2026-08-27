"""Tests for AudioService device lists, capture streams, and stream grouping."""

from __future__ import annotations

from unittest import mock

from shell.eventbus import EventBus
from shell.models import AudioDevice, AudioStream, Window, WorkspaceAudioState
from shell.servicios.audio.audio import AudioService
from shell.widgets.audio.audio_stream_row import group_streams_by_app


SAMPLE_SINKS = [
    {
        "index": 69,
        "name": "alsa_output.pci.analog-stereo",
        "description": "Analog Stereo",
        "properties": {"device.description": "Ryzen HD Audio"},
    },
]

SAMPLE_SOURCES = [
    {
        "index": 70,
        "name": "alsa_input.pci.analog-stereo",
        "description": "Analog Input",
        "properties": {},
    },
    {
        "index": 71,
        "name": "alsa_output.pci.analog-stereo.monitor",
        "description": "Monitor",
        "properties": {},
    },
]

SAMPLE_SINK_INPUT = {
    "index": 100,
    "sink": 69,
    "corked": False,
    "mute": False,
    "volume": {"mono": {"value_percent": "50%"}},
    "properties": {
        "application.name": "Firefox",
        "application.process.id": "1234",
        "media.name": "Example Track",
    },
}

SAMPLE_SOURCE_INPUT = {
    "index": 200,
    "source": 70,
    "mute": False,
    "volume": {"mono": {"value_percent": "80%"}},
    "properties": {
        "application.name": "Firefox",
        "application.process.id": "1234",
        "media.name": "Firefox",
    },
}


def _service_with_windows() -> AudioService:
    service = AudioService(EventBus())
    service._hyprland_windows = (
        Window(
            address="0xabc",
            app_class="firefox",
            title="Example Track",
            workspace_id=2,
            application_name="Firefox",
            icon="firefox",
            pid=1234,
        ),
    )
    return service


def test_query_output_devices_parses_pactl_json() -> None:
    service = AudioService(EventBus())
    with mock.patch.object(service, "_query_pactl_json", side_effect=lambda target: SAMPLE_SINKS if target == "sinks" else []):
        devices = service._query_output_devices()
    assert len(devices) == 1
    assert devices[0].kind == "output"
    assert devices[0].description == "Analog Stereo"


def test_query_input_devices_skips_monitors() -> None:
    service = AudioService(EventBus())
    with mock.patch.object(service, "_query_pactl_json", side_effect=lambda target: SAMPLE_SOURCES if target == "sources" else []):
        devices = service._query_input_devices()
    assert len(devices) == 1
    assert devices[0].name == "alsa_input.pci.analog-stereo"


def test_build_workspace_states_includes_playback_and_capture() -> None:
    service = _service_with_windows()
    output_by_id = {"69": AudioDevice(id="69", name="out", description="Analog Stereo", kind="output")}
    input_by_id = {"70": AudioDevice(id="70", name="in", description="Analog Input", kind="input")}

    states = service._build_workspace_states(
        [SAMPLE_SINK_INPUT],
        [SAMPLE_SOURCE_INPUT],
        output_by_id,
        input_by_id,
    )
    assert 2 in states
    streams = states[2].streams
    assert len(streams) == 2
    kinds = {stream.stream_kind for stream in streams}
    assert kinds == {"playback", "capture"}
    playback = next(stream for stream in streams if stream.stream_kind == "playback")
    capture = next(stream for stream in streams if stream.stream_kind == "capture")
    assert playback.device_name == "Analog Stereo"
    assert capture.device_name == "Analog Input"


def test_group_streams_by_app_merges_roles() -> None:
    playback = AudioStream(
        id="100",
        application_name="Firefox",
        title="Track",
        icon="firefox",
        volume=0.5,
        is_muted=False,
        is_playing=True,
        stream_kind="playback",
        device_id="69",
        device_name="Speakers",
        workspace_id=2,
        window_address="0xabc",
    )
    capture = AudioStream(
        id="200",
        application_name="Firefox",
        title="Firefox",
        icon="firefox",
        volume=0.8,
        is_muted=False,
        is_playing=True,
        stream_kind="capture",
        device_id="70",
        device_name="Mic",
        workspace_id=2,
        window_address="0xabc",
    )
    groups = group_streams_by_app((playback, capture))
    assert len(groups) == 1
    _key, grouped_playback, grouped_capture = groups[0]
    assert grouped_playback is playback
    assert grouped_capture is capture


def test_set_playback_sink_invokes_pactl_move() -> None:
    service = _service_with_windows()
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return mock.Mock(returncode=0)

    with mock.patch("shell.servicios.audio.audio.subprocess.run", fake_run):
        with mock.patch.object(service, "refresh", lambda: None):
            service.set_playback_sink("100", "69")
            import time
            time.sleep(0.08)
    assert calls and calls[0][:3] == ["pactl", "move-sink-input", "100"]


def test_capture_stream_kind_routes_volume_command() -> None:
    service = _service_with_windows()
    service._snapshot = service._snapshot.__class__(
        workspaces_audio=(
            WorkspaceAudioState(
                workspace_id=2,
                has_audio=True,
                is_playing=True,
                has_muted=False,
                streams=(
                    AudioStream(
                        id="200",
                        application_name="Firefox",
                        title="Firefox",
                        icon="firefox",
                        volume=0.5,
                        is_muted=False,
                        is_playing=True,
                        stream_kind="capture",
                        device_id="70",
                        device_name="Mic",
                        workspace_id=2,
                        window_address="0xabc",
                    ),
                ),
            ),
        ),
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return mock.Mock(returncode=0)

    with mock.patch("shell.servicios.audio.audio.subprocess.run", fake_run):
        service.set_stream_volume("200", 0.25)
        import time
        time.sleep(0.08)
    assert calls and calls[0][:3] == ["pactl", "set-source-input-volume", "200"]


if __name__ == "__main__":
    test_query_output_devices_parses_pactl_json()
    test_query_input_devices_skips_monitors()
    test_build_workspace_states_includes_playback_and_capture()
    test_group_streams_by_app_merges_roles()
    test_set_playback_sink_invokes_pactl_move()
    test_capture_stream_kind_routes_volume_command()
    print("audio safe tests OK")
