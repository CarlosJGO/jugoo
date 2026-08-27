"""Tests for audio visualizer bar computation and visibility rules."""

from __future__ import annotations

import math
import struct
from unittest import mock

from shell.eventbus import EventBus
from shell.models import (
    AudioDevice,
    AudioSnapshot,
    AudioStream,
    AudioVisualizerSnapshot,
    MediaPlayerSnapshot,
    MediaSnapshot,
    WorkspaceAudioState,
)
from shell.servicios.audio.audio_levels import (
    band_energies_from_magnitudes,
    compute_bars_from_pcm,
    compute_spectrum_frame,
    decay_bars,
    empty_bars,
    fft_magnitudes,
    frequency_to_rgba,
    logarithmic_band_centers,
    merge_visualizer_snapshot,
    pcm_rms,
    pcm_s16le_mono_samples,
    smooth_levels,
    update_peaks,
    visualizer_is_visible,
    visualizer_should_sample,
)
from shell.servicios.audio.audio_visualizer import AudioVisualizerService
from shell.servicios.multimedia.media import MediaService
from shell.servicios.audio.pipewire_monitor import (
    PWCAT_CAPTURE_PROPERTIES,
    pwcat_record_command,
    query_default_sink,
    query_default_sink_monitor,
    resolve_monitor_target,
    sink_node_name,
)


def _player(*, status: str = "playing", title: str = "Track") -> MediaPlayerSnapshot:
    return MediaPlayerSnapshot(
        bus_name="org.mpris.MediaPlayer2.firefox",
        identity="Firefox",
        title=title,
        artist="Artist",
        album="Album",
        art_url="",
        artwork_path="",
        status=status,
        position_usec=0,
        duration_usec=180_000_000,
        can_play=True,
        can_pause=True,
        can_go_next=True,
        can_go_previous=True,
        can_seek=True,
    )


def _pcm_sine(*, amplitude: int, sample_count: int, freq_hz: float = 440.0, rate: int = 16000) -> bytes:
    samples = [
        int(amplitude * math.sin(2.0 * math.pi * freq_hz * index / rate))
        for index in range(sample_count)
    ]
    return struct.pack(f"<{sample_count}h", *samples)


def _audio_snapshot_for_firefox(*, sink_name: str = "alsa_output.test") -> AudioSnapshot:
    stream = AudioStream(
        id="42",
        application_name="Firefox",
        title="Track",
        icon="firefox",
        volume=1.0,
        is_muted=False,
        is_playing=True,
        stream_kind="playback",
        device_id="7",
        device_name="Analog Stereo",
        workspace_id=1,
        window_address="0x1",
    )
    return AudioSnapshot(
        workspaces_audio=(
            WorkspaceAudioState(
                workspace_id=1,
                has_audio=True,
                is_playing=True,
                has_muted=False,
                streams=(stream,),
            ),
        ),
        output_devices=(
            AudioDevice(
                id="7",
                name=sink_name,
                description="Analog Stereo",
                kind="output",
            ),
        ),
    )


def test_pcm_s16le_mono_samples_reads_signed_shorts() -> None:
    pcm = struct.pack("<3h", 0, 1000, -1000)
    assert pcm_s16le_mono_samples(pcm) == (0, 1000, -1000)


def test_pcm_rms_reports_energy() -> None:
    silent = pcm_rms(b"\x00\x00" * 32)
    loud = pcm_rms(_pcm_sine(amplitude=12000, sample_count=256))
    assert silent == 0.0
    assert loud > 1000.0


def test_fft_magnitudes_peaks_near_tone_bin() -> None:
    rate = 16000
    freq = 1000.0
    n = 512
    samples = [
        math.sin(2.0 * math.pi * freq * index / rate)
        for index in range(n)
    ]
    magnitudes = fft_magnitudes(samples)
    bin_hz = rate / n
    expected_bin = int(round(freq / bin_hz))
    peak_bin = max(range(1, len(magnitudes)), key=lambda index: magnitudes[index])
    assert abs(peak_bin - expected_bin) <= 2
    assert magnitudes[peak_bin] > magnitudes[1]


def test_logarithmic_band_centers_increase() -> None:
    centers = logarithmic_band_centers(8, sample_rate=16000)
    assert len(centers) == 8
    assert all(centers[index] < centers[index + 1] for index in range(7))
    assert centers[0] < 100.0
    assert centers[-1] > 1000.0


def test_band_energies_prefer_matching_frequency() -> None:
    rate = 16000
    n = 512
    low = [math.sin(2.0 * math.pi * 80 * i / rate) for i in range(n)]
    high = [math.sin(2.0 * math.pi * 4000 * i / rate) for i in range(n)]
    low_mags = fft_magnitudes(low)
    high_mags = fft_magnitudes(high)
    low_bands = band_energies_from_magnitudes(low_mags, bar_count=8, sample_rate=rate)
    high_bands = band_energies_from_magnitudes(high_mags, bar_count=8, sample_rate=rate)
    assert low_bands.index(max(low_bands)) < high_bands.index(max(high_bands))


def test_frequency_to_rgba_maps_bass_to_warm_and_treble_to_cool() -> None:
    bass = frequency_to_rgba(60.0, 0.8)
    treble = frequency_to_rgba(8000.0, 0.8)
    assert bass[0] > bass[2]  # red-ish
    assert treble[2] > treble[0]  # blue-ish
    assert bass[3] > 0.2
    assert treble[3] > 0.2


def test_smooth_levels_attack_faster_than_release() -> None:
    rising = smooth_levels((1.0,), (0.0,), attack=0.8, release=0.2)
    falling = smooth_levels((0.0,), (1.0,), attack=0.8, release=0.2)
    assert rising[0] > 0.7
    assert falling[0] > 0.7  # slow fall


def test_update_peaks_hold_then_fall() -> None:
    peaks = update_peaks((0.9,), (0.0,))
    assert peaks[0] == 0.9
    fallen = update_peaks((0.1,), peaks, fall=0.05)
    assert 0.1 < fallen[0] < 0.9


def test_compute_bars_from_pcm_reacts_to_audio_energy() -> None:
    silent = compute_bars_from_pcm(b"\x00\x00" * 64, bar_count=8, previous=empty_bars(8))
    loud = compute_bars_from_pcm(
        _pcm_sine(amplitude=20000, sample_count=1024, freq_hz=440.0),
        bar_count=8,
        previous=empty_bars(8),
        sample_rate=16000,
    )
    assert max(silent) < 0.05
    assert max(loud) > 0.1


def test_compute_spectrum_frame_varies_across_bands() -> None:
    rate = 16000
    samples = []
    for index in range(1024):
        value = int(
            18000 * math.sin(2.0 * math.pi * 120 * index / rate)
            + 14000 * math.sin(2.0 * math.pi * 3500 * index / rate)
        )
        samples.append(max(-32767, min(32767, value)))
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    bars, peaks, colors = compute_spectrum_frame(
        pcm,
        bar_count=8,
        previous_bars=empty_bars(8),
        previous_peaks=empty_bars(8),
        sample_rate=rate,
    )
    assert max(bars) > 0.1
    assert max(bars) - min(bars) > 0.02
    assert len(colors) == 8
    assert peaks[bars.index(max(bars))] >= bars[bars.index(max(bars))] - 1e-9


def test_compute_bars_from_pcm_applies_decay_on_empty_chunk() -> None:
    previous = (0.8, 0.6, 0.4, 0.2)
    decayed = compute_bars_from_pcm(b"", bar_count=4, previous=previous)
    assert all(value < previous[index] for index, value in enumerate(decayed))


def test_decay_bars_fades_levels() -> None:
    assert decay_bars((1.0, 0.5)) == (0.78, 0.39)


def test_visualizer_visibility_follows_mpris_playback_state() -> None:
    playing = MediaSnapshot(players=(_player(status="playing"),), active_player="org.mpris.MediaPlayer2.firefox")
    paused = MediaSnapshot(players=(_player(status="paused"),), active_player="org.mpris.MediaPlayer2.firefox")
    assert visualizer_should_sample(playing) is True
    assert visualizer_is_visible(playing) is True
    assert visualizer_should_sample(paused) is False
    assert visualizer_is_visible(paused) is False


def test_merge_visualizer_snapshot_keeps_energy_when_paused() -> None:
    paused = MediaSnapshot(players=(_player(status="paused"),), active_player="org.mpris.MediaPlayer2.firefox")
    visible, bars, peaks, colors = merge_visualizer_snapshot(
        paused,
        (0.9, 0.8, 0.7),
        bar_count=3,
    )
    assert visible is True
    assert bars == (0.9, 0.8, 0.7)
    assert len(colors) == 3
    assert peaks == (0.9, 0.8, 0.7)


def test_merge_visualizer_snapshot_hides_when_paused() -> None:
    paused = MediaSnapshot(players=(_player(status="paused"),), active_player="org.mpris.MediaPlayer2.firefox")
    visible, bars, peaks, colors = merge_visualizer_snapshot(
        paused,
        (0.0, 0.0, 0.0),
        bar_count=3,
    )
    assert visible is False
    assert bars == (0.0, 0.0, 0.0)
    assert peaks == (0.0, 0.0, 0.0)
    assert colors == ((0.0, 0.0, 0.0, 0.0),) * 3


def test_sink_node_name_strips_pulse_monitor_suffix() -> None:
    assert sink_node_name("alsa_output.pci.analog-stereo") == "alsa_output.pci.analog-stereo"
    assert sink_node_name("alsa_output.pci.analog-stereo.monitor") == "alsa_output.pci.analog-stereo"
    assert sink_node_name("  alsa_output.test.monitor\n") == "alsa_output.test"


def test_resolve_monitor_target_uses_active_player_sink() -> None:
    media = MediaSnapshot(
        players=(_player(status="playing"),),
        active_player="org.mpris.MediaPlayer2.firefox",
    )
    audio = _audio_snapshot_for_firefox(sink_name="alsa_output.pci.analog-stereo")
    target = resolve_monitor_target(media, audio)
    assert target == "alsa_output.pci.analog-stereo"
    assert not target.endswith(".monitor")


def test_resolve_monitor_target_does_not_build_monitor_alias() -> None:
    media = MediaSnapshot(
        players=(_player(status="playing"),),
        active_player="org.mpris.MediaPlayer2.firefox",
    )
    audio = _audio_snapshot_for_firefox(sink_name="alsa_output.pci.analog-stereo.monitor")
    target = resolve_monitor_target(media, audio)
    assert target == "alsa_output.pci.analog-stereo"
    assert ".monitor" not in target


def test_resolve_monitor_target_falls_back_to_default() -> None:
    media = MediaSnapshot(
        players=(_player(status="playing"),),
        active_player="org.mpris.MediaPlayer2.firefox",
    )
    audio = AudioSnapshot(workspaces_audio=(), output_devices=())
    with mock.patch(
        "shell.servicios.audio.pipewire_monitor.query_default_sink",
        return_value="alsa_output.default",
    ):
        assert resolve_monitor_target(media, audio) == "alsa_output.default"


def test_query_default_sink_returns_node_name() -> None:
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(stdout="alsa_output.test\n", returncode=0)
        assert query_default_sink() == "alsa_output.test"
        assert query_default_sink_monitor() == "alsa_output.test"


def test_query_default_sink_strips_monitor_suffix() -> None:
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(
            stdout="alsa_output.test.monitor\n",
            returncode=0,
        )
        assert query_default_sink() == "alsa_output.test"


def test_pwcat_record_command_targets_sink_and_captures_monitor() -> None:
    command = pwcat_record_command(
        "alsa_output.pci-0000_30_00.6.analog-stereo",
        rate=16000,
        channels=1,
        latency=256,
    )
    assert command[:2] == ["pw-cat", "--record"]
    assert command[command.index("--target") + 1] == "alsa_output.pci-0000_30_00.6.analog-stereo"
    assert command[command.index("--properties") + 1] == PWCAT_CAPTURE_PROPERTIES
    assert "stream.capture.sink = true" in PWCAT_CAPTURE_PROPERTIES
    assert "stream.monitor = true" in PWCAT_CAPTURE_PROPERTIES
    assert not any(arg.endswith(".monitor") for arg in command)


def test_pwcat_record_command_strips_monitor_from_target() -> None:
    command = pwcat_record_command(
        "alsa_output.pci.analog-stereo.monitor",
        rate=16000,
        channels=1,
        latency=256,
    )
    assert command[command.index("--target") + 1] == "alsa_output.pci.analog-stereo"


def test_visualizer_service_starts_sampling_only_when_playing() -> None:
    event_bus = EventBus()
    media = MediaService(event_bus)
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=())
    visualizer = AudioVisualizerService(event_bus, media, audio)
    player = _player(status="playing")
    media._snapshot = MediaSnapshot(players=(player,), active_player=player.bus_name)

    with mock.patch.object(visualizer, "_sampler_loop"), mock.patch(
        "shell.servicios.audio.audio_visualizer.threading.Thread",
    ) as thread_cls:
        thread = mock.Mock()
        thread.is_alive.return_value = False
        thread_cls.return_value = thread
        visualizer._sync_sampling(force=True)

    assert visualizer._sampler_enabled is True
    thread_cls.assert_called_once()
    thread.start.assert_called_once()


def test_visualizer_service_keeps_bars_when_paused_until_decayed() -> None:
    event_bus = EventBus()
    media = MediaService(event_bus)
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=())
    visualizer = AudioVisualizerService(event_bus, media, audio)
    player = _player(status="paused")
    media._snapshot = MediaSnapshot(players=(player,), active_player=player.bus_name)
    visualizer._bars = (0.8, 0.6, 0.4, 0.2)

    with mock.patch.object(visualizer, "_sampler_loop"), mock.patch(
        "shell.servicios.audio.audio_visualizer.threading.Thread",
    ) as thread_cls:
        thread = mock.Mock()
        thread.is_alive.return_value = False
        thread_cls.return_value = thread
        visualizer._sync_sampling(force=True)

    assert visualizer.snapshot.visible is True
    assert visualizer.snapshot.bars[0] == 0.8
    thread.start.assert_called_once()


def test_visualizer_service_publishes_hidden_snapshot_when_paused() -> None:
    event_bus = EventBus()
    seen: list[AudioVisualizerSnapshot] = []
    event_bus.subscribe("audio_visualizer_changed", seen.append)
    media = MediaService(event_bus)
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=())
    visualizer = AudioVisualizerService(event_bus, media, audio)
    player = _player(status="paused")
    media._snapshot = MediaSnapshot(players=(player,), active_player=player.bus_name)

    visualizer._sync_sampling(force=True)

    assert visualizer.snapshot.visible is False
    assert visualizer.snapshot.bars == empty_bars(len(visualizer.snapshot.bars))


def test_visualizer_service_emits_updated_bars() -> None:
    event_bus = EventBus()
    seen: list[AudioVisualizerSnapshot] = []
    event_bus.subscribe("audio_visualizer_changed", seen.append)
    media = MediaService(event_bus)
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=())
    visualizer = AudioVisualizerService(event_bus, media, audio)
    player = _player(status="playing")
    media._snapshot = MediaSnapshot(players=(player,), active_player=player.bus_name)
    bars = tuple(0.1 + 0.05 * index for index in range(14))

    visualizer._bars = bars
    visualizer._peaks = bars
    visualizer._publish_snapshot(force=True)

    assert seen
    assert seen[-1].visible is True
    assert seen[-1].bars == bars
    assert len(seen[-1].colors) == 14


def test_visualizer_starts_pwcat_with_sink_capture_properties() -> None:
    event_bus = EventBus()
    media = MediaService(event_bus)
    audio = mock.Mock()
    audio.snapshot = _audio_snapshot_for_firefox(
        sink_name="alsa_output.pci-0000_30_00.6.analog-stereo",
    )
    visualizer = AudioVisualizerService(event_bus, media, audio)
    player = _player(status="playing")
    media._snapshot = MediaSnapshot(players=(player,), active_player=player.bus_name)
    visualizer._monitor_target = resolve_monitor_target(media.snapshot, audio.snapshot)

    with mock.patch("shell.servicios.audio.audio_visualizer.subprocess.Popen") as popen:
        process = mock.Mock()
        process.poll.return_value = None
        popen.return_value = process
        visualizer._ensure_pwcat_process()

    command = popen.call_args.args[0]
    assert command[command.index("--target") + 1] == "alsa_output.pci-0000_30_00.6.analog-stereo"
    assert command[command.index("--properties") + 1] == PWCAT_CAPTURE_PROPERTIES
    assert not any(arg.endswith(".monitor") for arg in command)


if __name__ == "__main__":
    test_pcm_s16le_mono_samples_reads_signed_shorts()
    test_pcm_rms_reports_energy()
    test_fft_magnitudes_peaks_near_tone_bin()
    test_logarithmic_band_centers_increase()
    test_band_energies_prefer_matching_frequency()
    test_frequency_to_rgba_maps_bass_to_warm_and_treble_to_cool()
    test_smooth_levels_attack_faster_than_release()
    test_update_peaks_hold_then_fall()
    test_compute_bars_from_pcm_reacts_to_audio_energy()
    test_compute_spectrum_frame_varies_across_bands()
    test_compute_bars_from_pcm_applies_decay_on_empty_chunk()
    test_decay_bars_fades_levels()
    test_visualizer_visibility_follows_mpris_playback_state()
    test_merge_visualizer_snapshot_keeps_energy_when_paused()
    test_merge_visualizer_snapshot_hides_when_paused()
    test_sink_node_name_strips_pulse_monitor_suffix()
    test_resolve_monitor_target_uses_active_player_sink()
    test_resolve_monitor_target_does_not_build_monitor_alias()
    test_resolve_monitor_target_falls_back_to_default()
    test_query_default_sink_returns_node_name()
    test_query_default_sink_strips_monitor_suffix()
    test_pwcat_record_command_targets_sink_and_captures_monitor()
    test_pwcat_record_command_strips_monitor_from_target()
    test_visualizer_service_starts_sampling_only_when_playing()
    test_visualizer_service_keeps_bars_when_paused_until_decayed()
    test_visualizer_service_publishes_hidden_snapshot_when_paused()
    test_visualizer_service_emits_updated_bars()
    test_visualizer_starts_pwcat_with_sink_capture_properties()
    print("audio levels/visualizer tests OK")
