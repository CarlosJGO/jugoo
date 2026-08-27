"""MPRIS-backed media player monitoring and transport controls."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

_DBUS_TIMEOUT_MS = 3_000

from ...config import (
    MEDIA_DBUS_PROPERTIES,
    MEDIA_MPRIS_PREFIX,
    MEDIA_OBJECT_PATH,
    MEDIA_PLAYER_INTERFACE,
    MEDIA_POSITION_POLL_MS,
    MEDIA_REFRESH_DEBOUNCE_MS,
    MEDIA_TRACK_CHANGE_REFRESH_MS,
    MEDIA_ROOT_INTERFACE,
)
from ...eventbus import EventBus
from ...models import MediaPlayerSnapshot, MediaSnapshot
from .media_artwork import MediaArtworkCache

MEDIA_CHANGED = "media_changed"
MEDIA_AUTO_PLAYER_ID = "__auto__"

DBUS_BUS_NAME = "org.freedesktop.DBus"
DBUS_OBJECT_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"

_logger = logging.getLogger(__name__)

_SELECTION_PROPERTIES = frozenset({"PlaybackStatus"})
_CONTENT_PROPERTIES = frozenset(
    {
        "Metadata",
        "Identity",
        "CanPlay",
        "CanPause",
        "CanGoNext",
        "CanGoPrevious",
        "CanSeek",
    },
)
_POSITION_PROPERTIES = frozenset({"Position"})


def normalize_playback_status(raw: Any) -> str:
    value = str(raw or "Stopped").strip().lower()
    if value == "playing":
        return "playing"
    if value == "paused":
        return "paused"
    return "stopped"


def metadata_field(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, GLib.Variant):
            value = value.unpack()
        if isinstance(value, (list, tuple)):
            parts = [str(item).strip() for item in value if str(item).strip()]
            if parts:
                return ", ".join(parts)
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def metadata_int(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, GLib.Variant):
        value = value.unpack()
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_metadata_variant(metadata: Any) -> dict[str, Any]:
    if metadata is None:
        return {}
    if isinstance(metadata, GLib.Variant):
        metadata = metadata.unpack()
    if not isinstance(metadata, dict):
        return {}
    parsed: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, GLib.Variant):
            value = value.unpack()
        parsed[str(key)] = value
    return parsed


def dbus_property_names(changed_properties: Any, invalidated_properties: Any = None) -> set[str]:
    """Extract D-Bus property names from a PropertiesChanged payload."""
    names: set[str] = set()
    unpacked = changed_properties
    if isinstance(changed_properties, GLib.Variant):
        unpacked = changed_properties.unpack()
    if isinstance(unpacked, dict):
        names.update(str(key) for key in unpacked)
    if invalidated_properties:
        names.update(str(name) for name in invalidated_properties)
    return names


def _player_auto_sort_key(
    player: MediaPlayerSnapshot,
    activity_rank: dict[str, float],
) -> tuple[int, int, float, str]:
    proxy_penalty = 1 if player.bus_name.endswith(".playerctld") else 0
    status_rank = {"playing": 0, "paused": 1, "stopped": 2}.get(player.status, 3)
    activity = activity_rank.get(player.bus_name, 0.0)
    return (proxy_penalty, status_rank, -activity, player.bus_name)


def select_active_player_bus_name(
    players: tuple[MediaPlayerSnapshot, ...],
    *,
    manual: str | None,
    previous: str | None,
    activity_rank: dict[str, float],
) -> str | None:
    """Pick the active player, honoring a sticky manual override when present."""
    if not players:
        return None

    available = {player.bus_name for player in players}

    if manual and manual in available:
        return manual

    playing = [player for player in players if player.status == "playing"]
    if playing:
        return sorted(
            playing,
            key=lambda player: _player_auto_sort_key(player, activity_rank),
        )[0].bus_name

    if previous and previous in available:
        return previous

    return sorted(
        players,
        key=lambda player: _player_auto_sort_key(player, activity_rank),
    )[0].bus_name


def compose_media_snapshot(
    players: tuple[MediaPlayerSnapshot, ...],
    *,
    manual: str | None,
    previous: str | None,
    activity_rank: dict[str, float],
) -> MediaSnapshot:
    active = select_active_player_bus_name(
        players,
        manual=manual,
        previous=previous,
        activity_rank=activity_rank,
    )
    return MediaSnapshot(players=players, active_player=active)


class _PlayerState:
    __slots__ = (
        "bus_name",
        "root_proxy",
        "player_proxy",
        "property_signal_id",
        "root_property_signal_id",
        "player_dbus_signal_id",
        "last_activity",
        "artwork_path",
    )

    def __init__(
        self,
        bus_name: str,
        root_proxy: Gio.DBusProxy,
        player_proxy: Gio.DBusProxy,
        property_signal_id: int,
        root_property_signal_id: int = 0,
        player_dbus_signal_id: int = 0,
    ) -> None:
        self.bus_name = bus_name
        self.root_proxy = root_proxy
        self.player_proxy = player_proxy
        self.property_signal_id = property_signal_id
        self.root_property_signal_id = root_property_signal_id
        self.player_dbus_signal_id = player_dbus_signal_id
        self.last_activity = time.monotonic()
        self.artwork_path = ""


class MediaService:
    """Single source of truth for MPRIS players via session D-Bus signals."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._snapshot = MediaSnapshot.empty()
        self._started = False
        self._bus: Gio.DBusConnection | None = None
        self._players: dict[str, _PlayerState] = {}
        self._manual_player: str | None = None
        self._activity_rank: dict[str, float] = {}
        self._artwork_cache = MediaArtworkCache()
        self._artwork_paths: dict[str, str] = {}
        self._artwork_requests: dict[str, str] = {}
        self._refresh_source_id = 0
        self._position_source_id = 0
        self._track_refresh_source_id = 0
        self._name_owner_signal_id = 0

    @property
    def snapshot(self) -> MediaSnapshot:
        return self._snapshot

    @property
    def manual_player(self) -> str | None:
        return self._manual_player

    @property
    def auto_player_selection(self) -> bool:
        return self._manual_player is None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            _logger.warning("Session D-Bus unavailable for MPRIS: %s", exc.message)
            return

        self._name_owner_signal_id = self._bus.signal_subscribe(
            DBUS_BUS_NAME,
            DBUS_INTERFACE,
            "NameOwnerChanged",
            DBUS_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_name_owner_changed,
        )
        self._discover_existing_players()
        self._refresh_snapshot(emit=True)

    def close(self) -> None:
        self._started = False
        self._cancel_refresh()
        self._cancel_position_poll()
        self._cancel_track_change_refresh()
        for state in list(self._players.values()):
            self._remove_player_state(state.bus_name)
        if self._bus is not None and self._name_owner_signal_id:
            self._bus.signal_unsubscribe(self._name_owner_signal_id)
            self._name_owner_signal_id = 0
        self._bus = None
        self._artwork_cache.close()

    def set_active_player(self, bus_name: str) -> None:
        if bus_name and bus_name not in self._players:
            return
        self._manual_player = bus_name or None
        self._refresh_snapshot(emit=True)

    def set_auto_player_selection(self) -> None:
        if self._manual_player is None:
            return
        self._manual_player = None
        self._refresh_snapshot(emit=True)

    def play_pause(self) -> None:
        self._call_active_player("PlayPause")

    def play(self) -> None:
        self._call_active_player("Play")

    def pause(self) -> None:
        self._call_active_player("Pause")

    def stop(self) -> None:
        self._call_active_player("Stop")

    def next_track(self) -> None:
        self._transport_with_track_refresh("Next")

    def previous_track(self) -> None:
        self._transport_with_track_refresh("Previous")

    def _transport_with_track_refresh(self, method: str) -> None:
        active = self.snapshot.active_player
        if active is None:
            return
        state = self._players.get(active)
        if state is None:
            return

        def run_transport() -> bool:
            self._player_method_idle(state.player_proxy, method)
            self._schedule_track_change_refresh()
            return False

        GLib.idle_add(run_transport)

    def seek(self, offset_usec: int) -> None:
        if self._bus is None:
            return
        active = self.snapshot.active
        if active is None or not active.can_seek:
            return
        state = self._players.get(active.bus_name)
        if state is None:
            return
        GLib.idle_add(self._seek_idle, state.player_proxy, int(offset_usec))

    def seek_to(self, position_usec: int) -> None:
        active = self.snapshot.active
        if active is None:
            return
        delta = int(position_usec) - int(active.position_usec)
        if delta != 0:
            self.seek(delta)

    def _call_active_player(self, method: str) -> None:
        active = self.snapshot.active_player
        if active is None:
            return
        state = self._players.get(active)
        if state is None:
            return
        GLib.idle_add(self._player_method_idle, state.player_proxy, method)

    @staticmethod
    def _player_method_idle(proxy: Gio.DBusProxy, method: str) -> bool:
        try:
            proxy.call_sync(
                method,
                None,
                Gio.DBusCallFlags.NONE,
                _DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            _logger.debug("MPRIS %s failed: %s", method, exc.message)
        return False

    def _seek_idle(self, proxy: Gio.DBusProxy, offset_usec: int) -> bool:
        try:
            proxy.call_sync(
                "Seek",
                GLib.Variant("(x)", (offset_usec,)),
                Gio.DBusCallFlags.NONE,
                _DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            _logger.debug("MPRIS Seek failed: %s", exc.message)
            return False
        GLib.timeout_add(50, self._refresh_position_idle)
        return False

    def _refresh_position_idle(self) -> bool:
        self._refresh_snapshot(emit=True, position_only=True)
        return False

    def _discover_existing_players(self) -> None:
        if self._bus is None:
            return
        try:
            result = self._bus.call_sync(
                DBUS_BUS_NAME,
                DBUS_OBJECT_PATH,
                DBUS_INTERFACE,
                "ListNames",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                _DBUS_TIMEOUT_MS,
                None,
            )
            for name in result.unpack()[0]:
                if str(name).startswith(MEDIA_MPRIS_PREFIX):
                    self._add_player(str(name))
        except GLib.Error as exc:
            _logger.debug("ListNames failed: %s", exc.message)

    def _on_name_owner_changed(
        self,
        _conn: Gio.DBusConnection,
        _sender: str | None,
        _path: str | None,
        _interface: str | None,
        _signal: str | None,
        params: GLib.Variant,
    ) -> None:
        name, _old_owner, new_owner = params.unpack()
        if not str(name).startswith(MEDIA_MPRIS_PREFIX):
            return
        bus_name = str(name)
        if new_owner:
            GLib.idle_add(self._add_player_idle, bus_name)
        else:
            GLib.idle_add(self._remove_player_idle, bus_name)

    def _add_player_idle(self, bus_name: str) -> bool:
        self._add_player(bus_name)
        return False

    def _remove_player_idle(self, bus_name: str) -> bool:
        self._remove_player_state(bus_name)
        return False

    def _add_player(self, bus_name: str) -> None:
        if self._bus is None or bus_name in self._players:
            return
        proxy_flags = Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES
        try:
            root_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                proxy_flags,
                None,
                bus_name,
                MEDIA_OBJECT_PATH,
                MEDIA_ROOT_INTERFACE,
                None,
            )
            player_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                proxy_flags,
                None,
                bus_name,
                MEDIA_OBJECT_PATH,
                MEDIA_PLAYER_INTERFACE,
                None,
            )
        except GLib.Error as exc:
            _logger.debug("Could not watch MPRIS player %s: %s", bus_name, exc.message)
            return

        player_props_id = player_proxy.connect(
            "g-properties-changed",
            self._on_g_properties_changed,
            bus_name,
        )
        root_props_id = root_proxy.connect(
            "g-properties-changed",
            self._on_g_properties_changed,
            bus_name,
        )
        player_signal_id = player_proxy.connect(
            "g-signal",
            self._on_player_dbus_signal,
            bus_name,
        )
        self._players[bus_name] = _PlayerState(
            bus_name,
            root_proxy,
            player_proxy,
            player_props_id,
            root_props_id,
            player_signal_id,
        )
        self._activity_rank[bus_name] = time.monotonic()
        _logger.debug(
            "mpris player added bus=%s selected=%s mode=%s",
            bus_name,
            self._snapshot.active_player,
            self._selection_mode(),
        )
        self._request_refresh(immediate=True, reason=f"player-added:{bus_name}")
        self._sync_position_poll()

    def _remove_player_state(self, bus_name: str) -> None:
        state = self._players.pop(bus_name, None)
        if state is None:
            return
        self._disconnect_player_signals(state)
        self._activity_rank.pop(bus_name, None)
        for art_url, path in list(self._artwork_paths.items()):
            if path == state.artwork_path:
                self._artwork_paths.pop(art_url, None)
        if self._manual_player == bus_name:
            self._manual_player = None
        _logger.debug(
            "mpris player removed bus=%s selected=%s mode=%s",
            bus_name,
            self._snapshot.active_player,
            self._selection_mode(),
        )
        self._request_refresh(immediate=True, reason=f"player-removed:{bus_name}")
        self._sync_position_poll()

    @staticmethod
    def _disconnect_player_signals(state: _PlayerState) -> None:
        pairs = (
            (state.player_proxy, getattr(state, "property_signal_id", 0)),
            (state.root_proxy, getattr(state, "root_property_signal_id", 0)),
            (state.player_proxy, getattr(state, "player_dbus_signal_id", 0)),
        )
        for proxy, signal_id in pairs:
            if not signal_id:
                continue
            try:
                proxy.disconnect(signal_id)
            except Exception:
                pass

    def _on_g_properties_changed(
        self,
        _proxy: Gio.DBusProxy,
        changed_properties: GLib.Variant,
        invalidated_properties: Any,
        bus_name: str,
    ) -> None:
        changed = dbus_property_names(changed_properties, invalidated_properties)
        self._handle_property_changes(bus_name, changed)

    def _on_player_dbus_signal(
        self,
        _proxy: Gio.DBusProxy,
        _sender_name: str,
        signal_name: str,
        _parameters: GLib.Variant,
        bus_name: str,
    ) -> None:
        if signal_name != "Seeked":
            return
        if bus_name != self._snapshot.active_player:
            return
        self._refresh_snapshot(emit=True, position_only=True)

    def _handle_property_changes(self, bus_name: str, changed: set[str]) -> None:
        if not changed or bus_name not in self._players:
            return

        selection_changed = bool(changed & _SELECTION_PROPERTIES)
        content_changed = bool(changed & _CONTENT_PROPERTIES)
        position_changed = bool(changed & _POSITION_PROPERTIES)
        if not (selection_changed or content_changed or position_changed):
            _logger.debug(
                "mpris ignore bus=%s changed=%s selected=%s mode=%s",
                bus_name,
                sorted(changed),
                self._snapshot.active_player,
                self._selection_mode(),
            )
            return

        _logger.debug(
            "mpris PropertiesChanged bus=%s changed=%s PlaybackStatus=%s selected=%s mode=%s",
            bus_name,
            sorted(changed),
            self._peek_playback_status(bus_name),
            self._snapshot.active_player,
            self._selection_mode(),
        )

        if selection_changed or content_changed:
            reason = "PlaybackStatus" if selection_changed else "Metadata"
            self._request_refresh(immediate=True, reason=f"{reason}:{bus_name}")
            return

        if position_changed and bus_name == self._snapshot.active_player:
            self._refresh_snapshot(emit=True, position_only=True)

    def _peek_playback_status(self, bus_name: str) -> str:
        state = self._players.get(bus_name)
        if state is None:
            return ""
        try:
            return normalize_playback_status(
                _cached_property(state.player_proxy, "PlaybackStatus", "Stopped"),
            )
        except Exception:
            return ""

    def _selection_mode(self) -> str:
        return "MANUAL" if self._manual_player else "AUTO"

    def _request_refresh(self, *, immediate: bool, reason: str = "") -> None:
        _logger.debug("media refresh requested immediate=%s reason=%s", immediate, reason)
        if immediate:
            self._cancel_refresh()
            self._refresh_snapshot(emit=True)
            return
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_source_id:
            return
        self._refresh_source_id = GLib.timeout_add(
            MEDIA_REFRESH_DEBOUNCE_MS,
            self._refresh_idle,
        )

    def _refresh_idle(self) -> bool:
        self._refresh_source_id = 0
        self._refresh_snapshot(emit=True)
        return False

    def _cancel_refresh(self) -> None:
        if self._refresh_source_id:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = 0

    def _schedule_track_change_refresh(self) -> None:
        self._cancel_track_change_refresh()
        self._track_refresh_source_id = GLib.timeout_add(
            MEDIA_TRACK_CHANGE_REFRESH_MS,
            self._track_change_refresh_idle,
        )

    def _track_change_refresh_idle(self) -> bool:
        self._track_refresh_source_id = 0
        self._cancel_refresh()
        self._refresh_snapshot(emit=True)
        return False

    def _cancel_track_change_refresh(self) -> None:
        if self._track_refresh_source_id:
            GLib.source_remove(self._track_refresh_source_id)
            self._track_refresh_source_id = 0

    def _sync_position_poll(self) -> None:
        active = self.snapshot.active
        should_poll = active is not None and active.status == "playing"
        if should_poll and not self._position_source_id:
            self._position_source_id = GLib.timeout_add(
                MEDIA_POSITION_POLL_MS,
                self._position_poll_tick,
            )
        elif not should_poll and self._position_source_id:
            GLib.source_remove(self._position_source_id)
            self._position_source_id = 0

    def _position_poll_tick(self) -> bool:
        self._refresh_snapshot(emit=True, position_only=True)
        active = self.snapshot.active
        if active is None or active.status != "playing":
            self._position_source_id = 0
            return False
        return True

    def _refresh_snapshot(self, *, emit: bool, position_only: bool = False) -> None:
        players: list[MediaPlayerSnapshot] = []
        for _bus_name, state in sorted(self._players.items()):
            snapshot = self._read_player_snapshot(state, position_only=position_only)
            if snapshot is not None:
                players.append(snapshot)
                if not position_only:
                    self._resolve_artwork(snapshot)

        if not position_only:
            self._bump_playing_activity(tuple(players))

        previous_active = self._snapshot.active_player
        next_snapshot = compose_media_snapshot(
            tuple(players),
            manual=self._manual_player,
            previous=previous_active,
            activity_rank=self._activity_rank,
        )

        changed = next_snapshot != self._snapshot
        if next_snapshot.active_player != previous_active:
            _logger.info(
                "%s PLAYER CHANGE: %s → %s",
                self._selection_mode(),
                previous_active or "-",
                next_snapshot.active_player or "-",
            )
        self._snapshot = next_snapshot
        self._sync_position_poll()
        if emit and changed:
            active = next_snapshot.active
            _logger.debug(
                "MEDIA_CHANGED emitted selected=%s mode=%s status=%s title=%s",
                next_snapshot.active_player,
                self._selection_mode(),
                None if active is None else active.status,
                None if active is None else active.title,
            )
            self._event_bus.emit(MEDIA_CHANGED, self._snapshot)
        elif emit:
            _logger.debug(
                "MEDIA_CHANGED skipped (unchanged) selected=%s mode=%s",
                next_snapshot.active_player,
                self._selection_mode(),
            )

    def _bump_playing_activity(self, players: tuple[MediaPlayerSnapshot, ...]) -> None:
        previous = {player.bus_name: player for player in self._snapshot.players}
        now = time.monotonic()
        for player in players:
            prior = previous.get(player.bus_name)
            if player.status == "playing" and (prior is None or prior.status != "playing"):
                self._activity_rank[player.bus_name] = now

    def _read_player_snapshot(
        self,
        state: _PlayerState,
        *,
        position_only: bool,
    ) -> MediaPlayerSnapshot | None:
        player = state.player_proxy
        root = state.root_proxy
        try:
            if position_only:
                if self._snapshot.active_player == state.bus_name:
                    previous = self._snapshot.active
                    if previous is None:
                        return None
                    position = _fresh_player_property_int(
                        player,
                        "Position",
                        previous.position_usec,
                    )
                    return replace(previous, position_usec=position)
                for existing in self._snapshot.players:
                    if existing.bus_name == state.bus_name:
                        return existing
                return None

            metadata = parse_metadata_variant(_cached_property(player, "Metadata", {}))
            status = normalize_playback_status(_cached_property(player, "PlaybackStatus", "Stopped"))
            identity = str(_cached_property(root, "Identity", "") or "").strip()
            if not identity:
                identity = state.bus_name.removeprefix(MEDIA_MPRIS_PREFIX)
            title = metadata_field(metadata, "xesam:title")
            artist = metadata_field(metadata, "xesam:artist", "xesam:albumArtist")
            album = metadata_field(metadata, "xesam:album")
            art_url = metadata_field(metadata, "mpris:artUrl", "xps:artUrl")
            duration = metadata_int(metadata, "mpris:length")
            position = _fresh_player_property_int(player, "Position", 0)
            artwork_path = self._artwork_paths.get(art_url, state.artwork_path)
            state.artwork_path = artwork_path
            return MediaPlayerSnapshot(
                bus_name=state.bus_name,
                identity=identity,
                title=title,
                artist=artist,
                album=album,
                art_url=art_url,
                artwork_path=artwork_path,
                status=status,
                position_usec=position,
                duration_usec=duration,
                can_play=bool(_cached_property(player, "CanPlay", False)),
                can_pause=bool(_cached_property(player, "CanPause", False)),
                can_go_next=bool(_cached_property(player, "CanGoNext", False)),
                can_go_previous=bool(_cached_property(player, "CanGoPrevious", False)),
                can_seek=bool(_cached_property(player, "CanSeek", False)),
            )
        except GLib.Error as exc:
            _logger.debug("Failed to read MPRIS player %s: %s", state.bus_name, exc.message)
            return None

    def _resolve_artwork(self, player: MediaPlayerSnapshot) -> None:
        if not player.art_url:
            return

        def _on_ready(_url: str, path: str) -> None:
            if not self._started or self._artwork_requests.get(player.bus_name) != _url:
                return
            self._artwork_paths[_url] = path
            state = self._players.get(player.bus_name)
            if state is not None:
                state.artwork_path = path
            GLib.idle_add(self._refresh_after_artwork)

        self._artwork_requests[player.bus_name] = player.art_url
        cached = self._artwork_cache.resolve(player.art_url, on_ready=_on_ready)
        if cached:
            self._artwork_paths[player.art_url] = cached

    def _refresh_after_artwork(self) -> bool:
        self._refresh_snapshot(emit=True)
        return False

    def _cancel_position_poll(self) -> None:
        if self._position_source_id:
            GLib.source_remove(self._position_source_id)
            self._position_source_id = 0


def _cached_property(proxy: Gio.DBusProxy, name: str, default: Any = None) -> Any:
    variant = proxy.get_cached_property(name)
    if variant is None:
        return default
    return variant.unpack()


def _cached_property_int(proxy: Gio.DBusProxy, name: str, default: int = 0) -> int:
    try:
        return int(_cached_property(proxy, name, default))
    except (TypeError, ValueError):
        return default


def _fresh_player_property_int(
    proxy: Gio.DBusProxy,
    name: str,
    default: int = 0,
) -> int:
    connection = proxy.get_connection()
    bus_name = proxy.get_name()
    if connection is None or not bus_name:
        return _cached_property_int(proxy, name, default)
    try:
        result = connection.call_sync(
            bus_name,
            MEDIA_OBJECT_PATH,
            MEDIA_DBUS_PROPERTIES,
            "Get",
            GLib.Variant("(ss)", (MEDIA_PLAYER_INTERFACE, name)),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            _DBUS_TIMEOUT_MS,
            None,
        )
        value = result.unpack()[0]
        if isinstance(value, GLib.Variant):
            return int(value.unpack())
        return int(value)
    except (GLib.Error, TypeError, ValueError):
        return _cached_property_int(proxy, name, default)
