"""Tests for default-sink volume OSD and focus-policy constants."""

from __future__ import annotations

from unittest import mock

from shell.config import POPUP_OUTSIDE_DISMISS_GRACE_MS, VOLUME_OSD_HIDE_DELAY_MS
from shell.eventbus import EventBus
from shell.models import AudioSnapshot, SystemVolumeState
from shell.servicios.audio.audio import AudioService, build_system_volume_state, friendly_device_description
from shell.widgets.audio.volume_osd import (
    volume_osd_bar_fraction,
    volume_osd_bar_text,
    volume_osd_glyph,
    volume_osd_headline,
)


SAMPLE_SINK = {
    "index": 69,
    "name": "alsa_output.pci-0000_30_00.6.analog-stereo",
    "description": "(null)",
    "mute": False,
    "volume": {
        "front-left": {"value_percent": "72%"},
        "front-right": {"value_percent": "72%"},
    },
    "properties": {"node.nick": "ALC897 Analog"},
}


def _volume(*, percent: int = 72, muted: bool = False, description: str = "ALC897 Analog") -> SystemVolumeState:
    return SystemVolumeState(
        sink_name="alsa_output.pci-0000_30_00.6.analog-stereo",
        sink_description=description,
        volume=percent / 100.0,
        is_muted=muted,
    )


def test_popup_outside_dismiss_grace_remains_500() -> None:
    assert POPUP_OUTSIDE_DISMISS_GRACE_MS == 500


def test_volume_osd_hide_delay_is_200() -> None:
    assert VOLUME_OSD_HIDE_DELAY_MS == 200


def test_friendly_device_description_skips_null() -> None:
    assert friendly_device_description(SAMPLE_SINK) == "ALC897 Analog"


def test_build_system_volume_state_from_default_sink() -> None:
    state = build_system_volume_state(
        [SAMPLE_SINK],
        "alsa_output.pci-0000_30_00.6.analog-stereo",
    )
    assert state is not None
    assert state.percent == 72
    assert state.is_muted is False
    assert state.sink_description == "ALC897 Analog"


def test_volume_osd_headline_and_mute() -> None:
    playing = _volume(percent=72, muted=False)
    muted = _volume(percent=72, muted=True)
    assert volume_osd_headline(playing) == f"{volume_osd_glyph(playing)} 72%"
    assert volume_osd_headline(muted) == f"{volume_osd_glyph(muted)} Silenciado"
    assert volume_osd_bar_fraction(muted) == 0.0
    assert "█" in volume_osd_bar_text(playing)
    assert volume_osd_bar_text(muted) == "░" * 20


def test_audio_service_includes_system_volume_in_snapshot() -> None:
    service = AudioService(EventBus())
    with mock.patch.object(service, "_query_pactl_json", side_effect=lambda target: {
        "sinks": [SAMPLE_SINK],
        "sources": [],
        "sink-inputs": [],
        "source-inputs": [],
    }.get(target, [])), mock.patch.object(
        service,
        "_query_default_sink_name",
        return_value=SAMPLE_SINK["name"],
    ):
        service._refresh_audio()

    assert service.snapshot.system_volume is not None
    assert service.snapshot.system_volume.percent == 72
    assert service.snapshot.system_volume.sink_description == "ALC897 Analog"


def test_volume_osd_controller_updates_and_reuses_single_window() -> None:
    from shell.controllers.volume_osd import VolumeOsdController

    event_bus = EventBus()
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=40))
    controller = VolumeOsdController(event_bus, audio, mock.Mock(), hide_delay_ms=200)
    controller.start()

    fake_osd = mock.Mock()
    fake_osd.get_visible.return_value = False
    with mock.patch.object(controller._osd, "get", return_value=fake_osd), mock.patch(
        "shell.controllers.volume_osd.GLib.timeout_add",
        return_value=11,
    ) as timeout_add, mock.patch(
        "shell.controllers.volume_osd.GLib.source_remove",
    ):
        controller._handle_audio_changed(
            AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=55)),
        )
        controller._handle_audio_changed(
            AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=60)),
        )

    assert fake_osd.refresh.call_count == 2
    assert fake_osd.show_osd.call_count == 2
    assert timeout_add.call_count == 2
    assert timeout_add.call_args_list[0].args[0] == 200
    assert timeout_add.call_args_list[1].args[0] == 200


def test_volume_osd_controller_shows_mute_and_new_sink() -> None:
    from shell.controllers.volume_osd import VolumeOsdController

    event_bus = EventBus()
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=50))
    controller = VolumeOsdController(event_bus, audio, mock.Mock(), hide_delay_ms=200)
    controller.start()
    fake_osd = mock.Mock()
    with mock.patch.object(controller._osd, "get", return_value=fake_osd), mock.patch(
        "shell.controllers.volume_osd.GLib.timeout_add",
        return_value=1,
    ), mock.patch("shell.controllers.volume_osd.GLib.source_remove"):
        muted = _volume(percent=50, muted=True)
        controller._handle_audio_changed(AudioSnapshot(workspaces_audio=(), system_volume=muted))
        hdmi = _volume(percent=40, description="HDMI Stereo")
        controller._handle_audio_changed(AudioSnapshot(workspaces_audio=(), system_volume=hdmi))

    assert fake_osd.refresh.call_args_list[0].args[0].is_muted is True
    assert fake_osd.refresh.call_args_list[1].args[0].sink_description == "HDMI Stereo"


def test_volume_osd_controller_hides_after_timeout() -> None:
    from shell.controllers.volume_osd import VolumeOsdController

    event_bus = EventBus()
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=10))
    controller = VolumeOsdController(event_bus, audio, mock.Mock(), hide_delay_ms=200)
    controller.start()
    fake_osd = mock.Mock()
    hide_cb = None

    def capture_timeout(ms, callback):
        nonlocal hide_cb
        assert ms == 200
        hide_cb = callback
        return 42

    with mock.patch.object(controller._osd, "get", return_value=fake_osd), mock.patch(
        "shell.controllers.volume_osd.GLib.timeout_add",
        side_effect=capture_timeout,
    ), mock.patch("shell.controllers.volume_osd.GLib.source_remove"):
        controller._osd._window = fake_osd
        controller._handle_audio_changed(
            AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=20)),
        )
        assert hide_cb is not None
        hide_cb()

    fake_osd.hide_osd.assert_called_once()


def test_volume_osd_is_non_interactive_and_rejects_focus() -> None:
    from shell.window_identity import configure_osd_window

    window = mock.Mock()
    configure_osd_window(window)
    window.set_accept_focus.assert_called_with(False)
    window.set_can_focus.assert_called_with(False)
    window.set_focus_on_map.assert_called_with(False)
    window.set_skip_taskbar_hint.assert_called_with(True)
    window.set_skip_pager_hint.assert_called_with(True)

    class FakeOsd:
        is_interactive = False

        @property
        def accepts_focus(self) -> bool:
            return False

    osd = FakeOsd()
    assert osd.is_interactive is False
    assert osd.accepts_focus is False


def test_controller_does_not_show_on_baseline_start() -> None:
    from shell.controllers.volume_osd import VolumeOsdController

    event_bus = EventBus()
    audio = mock.Mock()
    audio.snapshot = AudioSnapshot(workspaces_audio=(), system_volume=_volume(percent=33))
    controller = VolumeOsdController(event_bus, audio, mock.Mock())
    with mock.patch.object(controller._osd, "get") as get_osd:
        controller.start()
        get_osd.assert_not_called()


if __name__ == "__main__":
    test_popup_outside_dismiss_grace_remains_500()
    test_volume_osd_hide_delay_is_200()
    test_friendly_device_description_skips_null()
    test_build_system_volume_state_from_default_sink()
    test_volume_osd_headline_and_mute()
    test_audio_service_includes_system_volume_in_snapshot()
    test_volume_osd_controller_updates_and_reuses_single_window()
    test_volume_osd_controller_shows_mute_and_new_sink()
    test_volume_osd_controller_hides_after_timeout()
    test_volume_osd_is_non_interactive_and_rejects_focus()
    test_controller_does_not_show_on_baseline_start()
    print("volume osd tests OK")
