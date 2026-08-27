"""Safe tests for MediaService helpers and mocked MPRIS behavior."""

from __future__ import annotations

from dataclasses import replace
from unittest import mock

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

from shell.eventbus import EventBus
from shell.models import MediaPlayerSnapshot, MediaSnapshot
from shell.servicios.multimedia.media import (
    MediaService,
    compose_media_snapshot,
    dbus_property_names,
    metadata_field,
    normalize_playback_status,
    parse_metadata_variant,
    select_active_player_bus_name,
)
from shell.servicios.multimedia.media_artwork import MediaArtworkCache
from shell.config import MEDIA_ARTWORK_SIZE, MEDIA_POPUP_MAX_HEIGHT, MEDIA_POPUP_WIDTH, MEDIA_TRACK_CHANGE_REFRESH_MS
from shell.widgets.multimedia.media_format import (
    compact_bar_primary,
    compact_bar_secondary,
    format_media_time_usec,
    media_status_glyph,
    media_status_label,
)
from shell.widgets.multimedia.media_popup_layout import media_popup_dimensions


def _player(
    *,
    bus_name: str,
    identity: str,
    title: str = "Track",
    artist: str = "Artist",
    status: str = "paused",
) -> MediaPlayerSnapshot:
    return MediaPlayerSnapshot(
        bus_name=bus_name,
        identity=identity,
        title=title,
        artist=artist,
        album="Album",
        art_url="https://example.com/cover.jpg",
        artwork_path="",
        status=status,
        position_usec=30_000_000,
        duration_usec=180_000_000,
        can_play=True,
        can_pause=True,
        can_go_next=True,
        can_go_previous=True,
        can_seek=True,
    )


def test_normalize_playback_status() -> None:
    assert normalize_playback_status("Playing") == "playing"
    assert normalize_playback_status("Paused") == "paused"
    assert normalize_playback_status("Stopped") == "stopped"


def test_metadata_field_reads_string_and_list() -> None:
    metadata = {
        "xesam:title": "Song",
        "xesam:artist": ["Alice", "Bob"],
        "mpris:artUrl": GLib.Variant("s", "https://example.com/a.jpg"),
    }
    assert metadata_field(metadata, "xesam:title") == "Song"
    assert metadata_field(metadata, "xesam:artist") == "Alice, Bob"
    assert metadata_field(metadata, "mpris:artUrl") == "https://example.com/a.jpg"


def test_select_active_player_prefers_playing_over_paused() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.spotify", identity="Spotify", status="paused"),
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="playing"),
    )
    active = select_active_player_bus_name(
        players,
        manual=None,
        previous="org.mpris.MediaPlayer2.spotify",
        activity_rank={
            "org.mpris.MediaPlayer2.spotify": 100.0,
            "org.mpris.MediaPlayer2.firefox.instance_1": 1.0,
        },
    )
    assert active == "org.mpris.MediaPlayer2.firefox.instance_1"


def test_select_active_player_honors_manual_choice_among_playing() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.spotify", identity="Spotify", status="playing"),
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="playing"),
    )
    active = select_active_player_bus_name(
        players,
        manual="org.mpris.MediaPlayer2.firefox.instance_1",
        previous=None,
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.firefox.instance_1"


def test_manual_selection_not_overridden_by_other_playing() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="playing"),
        _player(bus_name="org.mpris.MediaPlayer2.strawberry", identity="Strawberry", status="playing"),
    )
    active = select_active_player_bus_name(
        players,
        manual="org.mpris.MediaPlayer2.strawberry",
        previous="org.mpris.MediaPlayer2.firefox.instance_1",
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.strawberry"


def test_manual_selection_keeps_paused_player_while_other_plays() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="playing"),
        _player(bus_name="org.mpris.MediaPlayer2.strawberry", identity="Strawberry", status="paused"),
    )
    active = select_active_player_bus_name(
        players,
        manual="org.mpris.MediaPlayer2.strawberry",
        previous="org.mpris.MediaPlayer2.firefox.instance_1",
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.strawberry"


def test_auto_mode_switches_when_new_player_starts_playing() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.strawberry", identity="Strawberry", status="paused"),
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="playing"),
    )
    active = select_active_player_bus_name(
        players,
        manual=None,
        previous="org.mpris.MediaPlayer2.strawberry",
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.firefox.instance_1"


def test_select_active_player_keeps_previous_when_none_playing() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.strawberry", identity="Strawberry", status="paused"),
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="stopped"),
    )
    active = select_active_player_bus_name(
        players,
        manual=None,
        previous="org.mpris.MediaPlayer2.strawberry",
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.strawberry"


def test_select_active_player_prefers_paused_over_stopped_without_previous() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="stopped"),
        _player(bus_name="org.mpris.MediaPlayer2.strawberry", identity="Strawberry", status="paused"),
    )
    active = select_active_player_bus_name(
        players,
        manual=None,
        previous=None,
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.strawberry"


def test_select_active_player_multiple_playing_is_deterministic() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_2", identity="Firefox", status="playing"),
        _player(bus_name="org.mpris.MediaPlayer2.firefox.instance_1", identity="Firefox", status="playing"),
    )
    active = select_active_player_bus_name(
        players,
        manual=None,
        previous=None,
        activity_rank={},
    )
    assert active == "org.mpris.MediaPlayer2.firefox.instance_1"


def test_select_active_player_avoids_playerctld_when_alternative_exists() -> None:
    players = (
        _player(bus_name="org.mpris.MediaPlayer2.playerctld", identity="playerctl", status="playing"),
        _player(bus_name="org.mpris.MediaPlayer2.vlc", identity="VLC", status="playing"),
    )
    active = select_active_player_bus_name(players, manual=None, previous=None, activity_rank={})
    assert active == "org.mpris.MediaPlayer2.vlc"


def test_compose_media_snapshot_empty_without_players() -> None:
    snapshot = compose_media_snapshot((), manual=None, previous=None, activity_rank={})
    assert snapshot == MediaSnapshot.empty()
    assert snapshot.has_media is False


def test_media_service_emits_on_player_add_and_remove() -> None:
    service = MediaService(EventBus())
    seen: list[MediaSnapshot] = []
    service._event_bus.subscribe("media_changed", seen.append)

    firefox = _player(
        bus_name="org.mpris.MediaPlayer2.firefox.instance_1",
        identity="Firefox",
        status="playing",
    )
    service._players["org.mpris.MediaPlayer2.firefox.instance_1"] = mock.Mock(
        bus_name=firefox.bus_name,
        root_proxy=object(),
        player_proxy=object(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    service._activity_rank[firefox.bus_name] = 1.0

    with mock.patch.object(service, "_read_player_snapshot", return_value=firefox):
        service._refresh_snapshot(emit=True)

    assert seen
    assert seen[-1].active is not None
    assert seen[-1].active.identity == "Firefox"

    service._remove_player_state(firefox.bus_name)
    with mock.patch.object(service, "_read_player_snapshot", return_value=None):
        service._refresh_snapshot(emit=True)

    assert service.snapshot.players == ()
    assert service.snapshot.active_player is None


def test_set_active_player_sets_manual_override() -> None:
    service = MediaService(EventBus())
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=object(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    player = _player(bus_name=bus_name, identity="Firefox", status="playing")
    service._activity_rank[bus_name] = 1.0

    with mock.patch.object(service, "_read_player_snapshot", return_value=player):
        service.set_active_player(bus_name)

    assert service.manual_player == bus_name
    assert service.snapshot.active_player == bus_name


def test_set_auto_player_selection_clears_manual_override() -> None:
    service = MediaService(EventBus())
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=object(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    player = _player(bus_name=bus_name, identity="Firefox", status="playing")
    service._manual_player = bus_name
    service._activity_rank[bus_name] = 1.0

    with mock.patch.object(service, "_read_player_snapshot", return_value=player):
        service.set_auto_player_selection()

    assert service.manual_player is None
    assert service.auto_player_selection is True


def test_manual_player_cleared_when_player_removed() -> None:
    service = MediaService(EventBus())
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=object(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    service._manual_player = bus_name
    service._remove_player_state(bus_name)
    assert service.manual_player is None


def test_position_poll_emits_media_changed_when_position_moves() -> None:
    service = MediaService(EventBus())
    seen: list[MediaSnapshot] = []
    service._event_bus.subscribe("media_changed", seen.append)
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    base = _player(
        bus_name=bus_name,
        identity="Firefox",
        status="playing",
    )
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=mock.Mock(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    service._snapshot = compose_media_snapshot(
        (base,),
        manual=None,
        previous=bus_name,
        activity_rank={},
    )

    moved = replace(base, position_usec=45_000_000)
    with mock.patch.object(service, "_read_player_snapshot", return_value=moved):
        service._refresh_snapshot(emit=True, position_only=True)

    assert seen
    assert seen[-1].active is not None
    assert seen[-1].active.position_usec == 45_000_000


def test_position_poll_stops_when_player_is_paused() -> None:
    service = MediaService(EventBus())
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    paused = _player(bus_name=bus_name, identity="Firefox", status="paused")
    service._snapshot = compose_media_snapshot(
        (paused,),
        manual=None,
        previous=bus_name,
        activity_rank={},
    )
    service._sync_position_poll()
    assert service._position_source_id == 0


def test_seek_to_calls_mpris_seek() -> None:
    service = MediaService(EventBus())
    service._bus = mock.Mock()
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    proxy = mock.Mock()
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=proxy,
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    player = _player(bus_name=bus_name, identity="Firefox", status="playing")
    service._snapshot = compose_media_snapshot(
        (player,),
        manual=bus_name,
        previous=bus_name,
        activity_rank={},
    )

    with mock.patch("shell.servicios.multimedia.media.GLib.idle_add", side_effect=lambda fn, *args: fn(*args) or False):
        service.seek_to(60_000_000)

    proxy.call_sync.assert_called_once()


def test_media_controller_refreshes_visible_popup_on_media_changed() -> None:
    from shell.controllers.media import MediaController

    class FakePopup:
        def __init__(self) -> None:
            self._visible = True
            self.refresh_calls: list[MediaSnapshot] = []

        def get_visible(self) -> bool:
            return self._visible

        def refresh(self, snapshot: MediaSnapshot) -> None:
            self.refresh_calls.append(snapshot)

    class FakeHandle:
        def __init__(self, popup: FakePopup) -> None:
            self._popup = popup

        @property
        def maybe(self) -> FakePopup:
            return self._popup

    service = MediaService(EventBus())
    popup = FakePopup()
    controller = MediaController(
        EventBus(),
        service,
        mock.Mock(get_anchor_widget=mock.Mock(return_value=object())),
        mock.Mock(),
    )
    controller._popup = FakeHandle(popup)
    snapshot = compose_media_snapshot(
        (_player(bus_name="org.mpris.MediaPlayer2.firefox", identity="Firefox", status="playing"),),
        manual=None,
        previous=None,
        activity_rank={},
    )
    controller._handle_media_changed(snapshot)
    assert popup.refresh_calls == [snapshot]


def test_set_active_player_updates_preferred_bus_name() -> None:
    service = MediaService(EventBus())
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=object(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    player = _player(bus_name=bus_name, identity="Firefox", status="playing")
    service._activity_rank[bus_name] = 1.0

    with mock.patch.object(service, "_read_player_snapshot", return_value=player):
        service.set_active_player(bus_name)

    assert service.snapshot.active_player == bus_name


YOUTUBE = "org.mpris.MediaPlayer2.firefox.instance_1"
STRAWBERRY = "org.mpris.MediaPlayer2.strawberry"


def _install_player(service: MediaService, player: MediaPlayerSnapshot) -> mock.Mock:
    state = mock.Mock(
        bus_name=player.bus_name,
        root_proxy=object(),
        player_proxy=mock.Mock(),
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    service._players[player.bus_name] = state
    service._activity_rank[player.bus_name] = 1.0
    return state


def _dual_player_harness(
    youtube_status: str,
    strawberry_status: str,
    *,
    youtube_title: str = "YouTube Track",
    strawberry_title: str = "Strawberry Track",
    manual: str | None = None,
) -> tuple[MediaService, list[MediaSnapshot], dict[str, MediaPlayerSnapshot]]:
    service = MediaService(EventBus())
    seen: list[MediaSnapshot] = []
    service._event_bus.subscribe("media_changed", seen.append)
    states = {
        YOUTUBE: _player(
            bus_name=YOUTUBE,
            identity="Firefox",
            status=youtube_status,
            title=youtube_title,
        ),
        STRAWBERRY: _player(
            bus_name=STRAWBERRY,
            identity="Strawberry",
            status=strawberry_status,
            title=strawberry_title,
        ),
    }
    for player in states.values():
        _install_player(service, player)
    service._manual_player = manual
    return service, seen, states


def _read_from(states: dict[str, MediaPlayerSnapshot]):
    def read_player(state, *, position_only=False):
        return states.get(state.bus_name)

    return read_player


def test_dbus_property_names_from_variant_and_invalidated() -> None:
    changed = GLib.Variant(
        "a{sv}",
        {"PlaybackStatus": GLib.Variant("s", "Playing")},
    )
    assert dbus_property_names(changed, ["Metadata"]) == {"PlaybackStatus", "Metadata"}


def test_auto_switches_youtube_playing_to_strawberry_playing() -> None:
    service, seen, states = _dual_player_harness("playing", "paused")
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        assert service.snapshot.active_player == YOUTUBE
        states[STRAWBERRY] = replace(states[STRAWBERRY], status="playing")
        service._handle_property_changes(STRAWBERRY, {"PlaybackStatus"})

    assert service.auto_player_selection is True
    assert service.snapshot.active_player == STRAWBERRY
    assert seen[-1].active is not None
    assert seen[-1].active.identity == "Strawberry"
    assert seen[-1].active.status == "playing"


def test_auto_switches_strawberry_playing_to_youtube_playing_and_stays() -> None:
    service, seen, states = _dual_player_harness("paused", "playing")
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        assert service.snapshot.active_player == STRAWBERRY
        states[YOUTUBE] = replace(states[YOUTUBE], status="playing")
        service._handle_property_changes(YOUTUBE, {"PlaybackStatus"})
        assert service.snapshot.active_player == YOUTUBE
        emitted = len(seen)
        states[STRAWBERRY] = replace(states[STRAWBERRY], title="Other Song")
        service._handle_property_changes(STRAWBERRY, {"Metadata"})

    assert service.snapshot.active_player == YOUTUBE
    assert len(seen) >= emitted
    strawberry = next(player for player in service.snapshot.players if player.bus_name == STRAWBERRY)
    assert strawberry.title == "Other Song"


def test_auto_selects_other_playing_when_active_pauses() -> None:
    service, seen, states = _dual_player_harness("playing", "paused")
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        states[STRAWBERRY] = replace(states[STRAWBERRY], status="playing")
        service._handle_property_changes(STRAWBERRY, {"PlaybackStatus"})
        assert service.snapshot.active_player == STRAWBERRY
        states[STRAWBERRY] = replace(states[STRAWBERRY], status="paused")
        service._handle_property_changes(STRAWBERRY, {"PlaybackStatus"})

    assert service.snapshot.active_player == YOUTUBE
    assert seen[-1].active is not None
    assert seen[-1].active.status == "playing"


def test_volume_property_does_not_steal_auto_selection() -> None:
    service, seen, states = _dual_player_harness("paused", "playing")
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        assert service.snapshot.active_player == STRAWBERRY
        emitted = len(seen)
        service._handle_property_changes(YOUTUBE, {"Volume"})

    assert service.snapshot.active_player == STRAWBERRY
    assert len(seen) == emitted


def test_manual_does_not_switch_when_other_player_starts() -> None:
    service, seen, states = _dual_player_harness("playing", "paused", manual=YOUTUBE)
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        states[STRAWBERRY] = replace(states[STRAWBERRY], status="playing")
        service._handle_property_changes(STRAWBERRY, {"PlaybackStatus"})

    assert service.manual_player == YOUTUBE
    assert service.snapshot.active_player == YOUTUBE
    assert seen[-1].active_player == YOUTUBE


def test_manual_player_removed_returns_to_auto() -> None:
    service, seen, states = _dual_player_harness("playing", "playing", manual=YOUTUBE)
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        assert service.snapshot.active_player == YOUTUBE
        service._remove_player_state(YOUTUBE)

    assert service.manual_player is None
    assert service.auto_player_selection is True
    assert service.snapshot.active_player == STRAWBERRY
    assert seen[-1].active is not None
    assert seen[-1].active.identity == "Strawberry"


class _FakeMediaPopup:
    def __init__(self) -> None:
        self.refresh_calls: list[MediaSnapshot] = []
        self.closed = False

    def get_visible(self) -> bool:
        return True

    def refresh(self, snapshot: MediaSnapshot) -> None:
        self.refresh_calls.append(snapshot)

    def close_popup(self) -> None:
        self.closed = True


class _FakePopupHandle:
    def __init__(self, popup: _FakeMediaPopup) -> None:
        self._popup = popup

    @property
    def maybe(self) -> _FakeMediaPopup:
        return self._popup


def _controller_with_popup(service: MediaService) -> tuple[object, _FakeMediaPopup]:
    from shell.controllers.media import MediaController

    popup = _FakeMediaPopup()
    controller = MediaController(
        service._event_bus,
        service,
        mock.Mock(get_anchor_widget=mock.Mock(return_value=object())),
        mock.Mock(),
    )
    controller._popup = _FakePopupHandle(popup)
    service._event_bus.subscribe("media_changed", controller._handle_media_changed)
    return controller, popup


def test_metadata_change_updates_visible_popup() -> None:
    service, _seen, states = _dual_player_harness("playing", "paused", youtube_title="Old Title")
    _controller, popup = _controller_with_popup(service)
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        states[YOUTUBE] = replace(
            states[YOUTUBE],
            title="New Title",
            artist="New Artist",
            album="New Album",
        )
        service._handle_property_changes(YOUTUBE, {"Metadata"})

    assert popup.closed is False
    assert popup.refresh_calls
    active = popup.refresh_calls[-1].active
    assert active is not None
    assert active.title == "New Title"
    assert active.artist == "New Artist"
    assert active.album == "New Album"


def test_track_change_updates_visible_popup_without_recreating() -> None:
    service, _seen, states = _dual_player_harness("playing", "paused", youtube_title="Track 1")
    _controller, popup = _controller_with_popup(service)
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        states[YOUTUBE] = replace(states[YOUTUBE], title="Track 2", duration_usec=240_000_000)
        service._handle_property_changes(YOUTUBE, {"Metadata"})

    assert popup.closed is False
    assert popup.refresh_calls[-1].active is not None
    assert popup.refresh_calls[-1].active.title == "Track 2"
    assert popup.refresh_calls[-1].active.duration_usec == 240_000_000


def test_auto_player_change_refreshes_popup_without_closing() -> None:
    service, _seen, states = _dual_player_harness("playing", "paused")
    _controller, popup = _controller_with_popup(service)
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        states[STRAWBERRY] = replace(states[STRAWBERRY], status="playing", title="Now Playing")
        service._handle_property_changes(STRAWBERRY, {"PlaybackStatus"})

    assert popup.closed is False
    assert popup.refresh_calls[-1].active_player == STRAWBERRY
    assert popup.refresh_calls[-1].active is not None
    assert popup.refresh_calls[-1].active.title == "Now Playing"


def test_active_window_receives_new_media_snapshot() -> None:
    service, _seen, states = _dual_player_harness("playing", "paused")
    received: list[MediaSnapshot] = []
    service._event_bus.subscribe("media_changed", received.append)
    read = _read_from(states)
    with mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read,
    ):
        service._refresh_snapshot(emit=True)
        states[STRAWBERRY] = replace(states[STRAWBERRY], status="playing", title="Bar Title")
        service._handle_property_changes(STRAWBERRY, {"PlaybackStatus"})

    assert received[-1].active is not None
    assert received[-1].active_player == STRAWBERRY
    assert received[-1].active.title == "Bar Title"


def _run_transport_timers():
    idle = mock.patch(
        "shell.servicios.multimedia.media.GLib.idle_add",
        side_effect=lambda fn: fn() or False,
    )
    timeout = mock.patch(
        "shell.servicios.multimedia.media.GLib.timeout_add",
        side_effect=lambda ms, cb: cb() or 1 if ms == MEDIA_TRACK_CHANGE_REFRESH_MS else 0,
    )
    return idle, timeout


def test_next_track_reselects_active_player_in_auto_mode() -> None:
    service = MediaService(EventBus())
    service._bus = mock.Mock()
    firefox = "org.mpris.MediaPlayer2.firefox.instance_1"
    strawberry = "org.mpris.MediaPlayer2.strawberry"
    initial = (
        _player(bus_name=firefox, identity="Firefox", status="playing", title="Old"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Other"),
    )
    updated = (
        _player(bus_name=firefox, identity="Firefox", status="paused", title="Old"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="New Track"),
    )
    for player in initial:
        _install_player(service, player)
    service._snapshot = compose_media_snapshot(
        initial,
        manual=None,
        previous=firefox,
        activity_rank={},
    )
    assert service.snapshot.active_player == firefox

    def read_player(state, *, position_only=False):
        for player in updated:
            if player.bus_name == state.bus_name:
                return player
        return None

    idle, timeout = _run_transport_timers()
    with idle, timeout, mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read_player,
    ):
        service.next_track()

    assert service.snapshot.active_player == strawberry
    assert service.snapshot.active is not None
    assert service.snapshot.active.title == "New Track"


def test_previous_track_reselects_active_player_in_auto_mode() -> None:
    service = MediaService(EventBus())
    service._bus = mock.Mock()
    firefox = "org.mpris.MediaPlayer2.firefox.instance_1"
    strawberry = "org.mpris.MediaPlayer2.strawberry"
    initial = (
        _player(bus_name=firefox, identity="Firefox", status="playing", title="Current"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Other"),
    )
    updated = (
        _player(bus_name=firefox, identity="Firefox", status="paused", title="Current"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Previous Track"),
    )
    for player in initial:
        _install_player(service, player)
    service._snapshot = compose_media_snapshot(
        initial,
        manual=None,
        previous=firefox,
        activity_rank={},
    )

    def read_player(state, *, position_only=False):
        for player in updated:
            if player.bus_name == state.bus_name:
                return player
        return None

    idle, timeout = _run_transport_timers()
    with idle, timeout, mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read_player,
    ):
        service.previous_track()

    assert service.snapshot.active_player == strawberry
    assert service.snapshot.active is not None
    assert service.snapshot.active.title == "Previous Track"


def test_next_track_keeps_manual_player_selection() -> None:
    service = MediaService(EventBus())
    service._bus = mock.Mock()
    firefox = "org.mpris.MediaPlayer2.firefox.instance_1"
    strawberry = "org.mpris.MediaPlayer2.strawberry"
    initial = (
        _player(bus_name=firefox, identity="Firefox", status="playing", title="Manual Track"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Other"),
    )
    updated = (
        _player(bus_name=firefox, identity="Firefox", status="playing", title="Manual Next"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Other"),
    )
    for player in initial:
        _install_player(service, player)
    service._manual_player = firefox
    service._snapshot = compose_media_snapshot(
        initial,
        manual=firefox,
        previous=firefox,
        activity_rank={},
    )

    def read_player(state, *, position_only=False):
        for player in updated:
            if player.bus_name == state.bus_name:
                return player
        return None

    idle, timeout = _run_transport_timers()
    with idle, timeout, mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read_player,
    ):
        service.next_track()

    assert service.snapshot.active_player == firefox
    assert service.snapshot.active is not None
    assert service.snapshot.active.title == "Manual Next"


def test_previous_track_keeps_manual_player_selection() -> None:
    service = MediaService(EventBus())
    service._bus = mock.Mock()
    firefox = "org.mpris.MediaPlayer2.firefox.instance_1"
    strawberry = "org.mpris.MediaPlayer2.strawberry"
    initial = (
        _player(bus_name=firefox, identity="Firefox", status="playing", title="Manual Track"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Other"),
    )
    updated = (
        _player(bus_name=firefox, identity="Firefox", status="playing", title="Manual Previous"),
        _player(bus_name=strawberry, identity="Strawberry", status="playing", title="Other"),
    )
    for player in initial:
        _install_player(service, player)
    service._manual_player = firefox
    service._snapshot = compose_media_snapshot(
        initial,
        manual=firefox,
        previous=firefox,
        activity_rank={},
    )

    def read_player(state, *, position_only=False):
        for player in updated:
            if player.bus_name == state.bus_name:
                return player
        return None

    idle, timeout = _run_transport_timers()
    with idle, timeout, mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        side_effect=read_player,
    ):
        service.previous_track()

    assert service.snapshot.active_player == firefox
    assert service.snapshot.active is not None
    assert service.snapshot.active.title == "Manual Previous"


def test_next_track_emits_media_changed_for_visible_popup() -> None:
    from shell.controllers.media import MediaController

    class FakePopup:
        def __init__(self) -> None:
            self.refresh_calls: list[MediaSnapshot] = []

        def get_visible(self) -> bool:
            return True

        def refresh(self, snapshot: MediaSnapshot) -> None:
            self.refresh_calls.append(snapshot)

    class FakeHandle:
        def __init__(self, popup: FakePopup) -> None:
            self._popup = popup

        @property
        def maybe(self) -> FakePopup:
            return self._popup

    service = MediaService(EventBus())
    service._bus = mock.Mock()
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    before = _player(bus_name=bus_name, identity="Firefox", status="playing", title="Before")
    after = _player(bus_name=bus_name, identity="Firefox", status="playing", title="After")
    _install_player(service, before)
    service._snapshot = compose_media_snapshot(
        (before,),
        manual=None,
        previous=bus_name,
        activity_rank={},
    )

    popup = FakePopup()
    controller = MediaController(
        EventBus(),
        service,
        mock.Mock(get_anchor_widget=mock.Mock(return_value=object())),
        mock.Mock(),
    )
    controller._popup = FakeHandle(popup)
    service._event_bus.subscribe(
        "media_changed",
        lambda snapshot: controller._handle_media_changed(snapshot),
    )

    idle, timeout = _run_transport_timers()
    with idle, timeout, mock.patch.object(service, "_resolve_artwork"), mock.patch.object(
        service,
        "_read_player_snapshot",
        return_value=after,
    ):
        service.next_track()

    assert popup.refresh_calls
    assert popup.refresh_calls[-1].active is not None
    assert popup.refresh_calls[-1].active.title == "After"


def test_play_pause_calls_mpris_method() -> None:
    service = MediaService(EventBus())
    bus_name = "org.mpris.MediaPlayer2.firefox.instance_1"
    proxy = mock.Mock()
    proxy.call_sync = mock.Mock()
    service._players[bus_name] = mock.Mock(
        bus_name=bus_name,
        root_proxy=object(),
        player_proxy=proxy,
        property_signal_id=1,
        last_activity=0.0,
        artwork_path="",
    )
    player = _player(bus_name=bus_name, identity="Firefox", status="playing")
    service._snapshot = compose_media_snapshot(
        (player,),
        manual=bus_name,
        previous=None,
        activity_rank={},
    )

    with mock.patch("shell.servicios.multimedia.media.GLib.idle_add", side_effect=lambda fn, *args: fn(*args) or False):
        service.play_pause()

    proxy.call_sync.assert_called_once()
    assert proxy.call_sync.call_args.args[0] == "PlayPause"


def test_media_popup_dimensions_are_standardized() -> None:
    width, max_height, artwork_size = media_popup_dimensions()
    assert width == MEDIA_POPUP_WIDTH == 440
    assert max_height == MEDIA_POPUP_MAX_HEIGHT == 300
    assert artwork_size == MEDIA_ARTWORK_SIZE == 128


def test_format_media_time_usec() -> None:
    assert format_media_time_usec(0) == "0:00"
    assert format_media_time_usec(65_000_000) == "1:05"
    assert format_media_time_usec(3_661_000_000) == "1:01:01"


def test_media_status_helpers() -> None:
    assert media_status_glyph("playing") == "▶"
    assert media_status_glyph("paused") == "⏸"
    assert media_status_label("playing") == "Reproduciendo"


def test_compact_bar_labels() -> None:
    player = _player(
        bus_name="org.mpris.MediaPlayer2.firefox",
        identity="Firefox",
        title="Video Title",
        artist="Channel Name",
    )
    assert compact_bar_primary(player) == "Video Title"
    assert "Channel Name" in compact_bar_secondary(player)


def test_artwork_cache_uses_existing_file_without_download() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        cache = MediaArtworkCache(cache_root=Path(tmp))
        art_url = "file:///tmp/example.jpg"
        target = cache._cache_path_for_url(art_url)
        target.write_bytes(b"fake")

        resolved = cache.resolve(art_url)
        assert resolved == str(target)


def test_parse_metadata_variant_from_glib_variant() -> None:
    metadata = GLib.Variant(
        "a{sv}",
        {
            "xesam:title": GLib.Variant("s", "Hello"),
            "mpris:length": GLib.Variant("x", 120_000_000),
        },
    )
    parsed = parse_metadata_variant(metadata)
    assert parsed["xesam:title"] == "Hello"
    assert parsed["mpris:length"] == 120_000_000


if __name__ == "__main__":
    test_normalize_playback_status()
    test_metadata_field_reads_string_and_list()
    test_select_active_player_prefers_playing_over_paused()
    test_select_active_player_honors_manual_choice_among_playing()
    test_manual_selection_not_overridden_by_other_playing()
    test_manual_selection_keeps_paused_player_while_other_plays()
    test_auto_mode_switches_when_new_player_starts_playing()
    test_select_active_player_keeps_previous_when_none_playing()
    test_select_active_player_prefers_paused_over_stopped_without_previous()
    test_select_active_player_multiple_playing_is_deterministic()
    test_select_active_player_avoids_playerctld_when_alternative_exists()
    test_compose_media_snapshot_empty_without_players()
    test_media_service_emits_on_player_add_and_remove()
    test_set_active_player_sets_manual_override()
    test_set_auto_player_selection_clears_manual_override()
    test_manual_player_cleared_when_player_removed()
    test_position_poll_emits_media_changed_when_position_moves()
    test_position_poll_stops_when_player_is_paused()
    test_seek_to_calls_mpris_seek()
    test_dbus_property_names_from_variant_and_invalidated()
    test_auto_switches_youtube_playing_to_strawberry_playing()
    test_auto_switches_strawberry_playing_to_youtube_playing_and_stays()
    test_auto_selects_other_playing_when_active_pauses()
    test_volume_property_does_not_steal_auto_selection()
    test_manual_does_not_switch_when_other_player_starts()
    test_manual_player_removed_returns_to_auto()
    test_metadata_change_updates_visible_popup()
    test_track_change_updates_visible_popup_without_recreating()
    test_auto_player_change_refreshes_popup_without_closing()
    test_active_window_receives_new_media_snapshot()
    test_next_track_reselects_active_player_in_auto_mode()
    test_previous_track_reselects_active_player_in_auto_mode()
    test_next_track_keeps_manual_player_selection()
    test_previous_track_keeps_manual_player_selection()
    test_next_track_emits_media_changed_for_visible_popup()
    test_media_controller_refreshes_visible_popup_on_media_changed()
    test_set_active_player_updates_preferred_bus_name()
    test_play_pause_calls_mpris_method()
    test_media_popup_dimensions_are_standardized()
    test_format_media_time_usec()
    test_media_status_helpers()
    test_compact_bar_labels()
    test_artwork_cache_uses_existing_file_without_download()
    test_parse_metadata_variant_from_glib_variant()
    print("media safe tests OK")
