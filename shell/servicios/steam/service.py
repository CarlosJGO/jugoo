"""Owns the installed Steam games catalog and the user's ignored appids."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
from typing import Callable, Iterable

from ... import config as shell_config
from ...eventbus import EventBus
from ...models import SteamCatalogSnapshot, SteamGame, normalize_steam_appid
from ...runtime_paths import steam_prefs_path
from .images import SteamArtwork, SteamArtworkCache
from .library import SteamScanResult, parse_library_paths, scan_steam_games
from .store import load_ignored_appids, save_ignored_appids, unique_appids

STEAM_GAMES_CHANGED = "steam_games_changed"
# Payload: the updated ``SteamGame``. Sent per cover so the UI swaps one card.
STEAM_GAME_ARTWORK_CHANGED = "steam_game_artwork_changed"
STEAM_GAMES_REFRESH_REQUESTED = "steam_games_refresh_requested"
STEAM_GAME_IGNORE_TOGGLE_REQUESTED = "steam_game_ignore_toggle_requested"
STEAM_SHOW_IGNORED_TOGGLE_REQUESTED = "steam_show_ignored_toggle_requested"

ExtraPathsProvider = Callable[[], Iterable[Path]]
Scanner = Callable[[Iterable[Path] | None, Iterable[Path]], SteamScanResult]


def _configured_extra_paths() -> tuple[Path, ...]:
    return parse_library_paths(getattr(shell_config, "STEAM_EXTRA_LIBRARY_PATHS", ""))


def parse_appid_list(value: object) -> tuple[str, ...]:
    """Appids from a settings string; ``;``, ``,`` and whitespace all separate."""
    text = str(value or "").replace(";", " ").replace(",", " ")
    return unique_appids(text.split())


def _configured_ignored() -> tuple[str, ...]:
    return parse_appid_list(getattr(shell_config, "STEAM_IGNORED_APPIDS", ""))


def _configured_show_ignored() -> bool:
    return bool(getattr(shell_config, "STEAM_SHOW_IGNORED", False))


def _apply_artwork(game: SteamGame, artwork: SteamArtwork | None) -> SteamGame:
    if artwork is None:
        return replace(game, image_pending=False)
    return replace(game, image_path=artwork.path, image_kind=artwork.kind, image_pending=False)


class SteamGamesService:
    """Single source of truth for installed Steam games; scans on request, never at import."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        prefs_path: Path | None = None,
        roots: tuple[Path, ...] | None = None,
        extra_paths: ExtraPathsProvider | None = None,
        scanner: Scanner | None = None,
        artwork: SteamArtworkCache | None = None,
        configured_ignored: Callable[[], tuple[str, ...]] = _configured_ignored,
        configured_show_ignored: Callable[[], bool] = _configured_show_ignored,
    ) -> None:
        self._event_bus = event_bus
        self._configured_ignored = configured_ignored
        self._configured_show_ignored = configured_show_ignored
        self._settings_ignored: tuple[str, ...] = ()
        self._settings_show_ignored = False
        self._prefs_path = prefs_path if prefs_path is not None else steam_prefs_path()
        self._roots = roots
        self._extra_paths = extra_paths or _configured_extra_paths
        self._scanner = scanner or scan_steam_games
        self._artwork = artwork if artwork is not None else SteamArtworkCache(roots=roots)
        self._lock = threading.RLock()
        self._scan: SteamScanResult = SteamScanResult()
        self._scanned = False
        self._ignored: tuple[str, ...] = ()
        self._include_ignored = False
        self._scan_running = False
        self._scan_pending = False
        # Downloads that finish while a rescan is still building its game list.
        self._ready_artwork: dict[str, SteamArtwork | None] = {}
        self._event_bus.subscribe(STEAM_GAMES_REFRESH_REQUESTED, self._on_refresh_requested)
        self._event_bus.subscribe(STEAM_GAME_IGNORE_TOGGLE_REQUESTED, self._on_ignore_toggle)
        self._event_bus.subscribe(STEAM_SHOW_IGNORED_TOGGLE_REQUESTED, self._on_show_ignored_toggle)

    @property
    def snapshot(self) -> SteamCatalogSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def start(self) -> None:
        with self._lock:
            self._ignored = load_ignored_appids(self._prefs_path)
            self._read_settings_locked()

    def close(self) -> None:
        self._event_bus.unsubscribe(STEAM_GAMES_REFRESH_REQUESTED, self._on_refresh_requested)
        self._event_bus.unsubscribe(STEAM_GAME_IGNORE_TOGGLE_REQUESTED, self._on_ignore_toggle)
        self._event_bus.unsubscribe(STEAM_SHOW_IGNORED_TOGGLE_REQUESTED, self._on_show_ignored_toggle)
        self._artwork.close()

    def refresh(self) -> SteamCatalogSnapshot:
        """Blocking rescan. UI callers use :meth:`refresh_async`."""
        result = self._scanner(self._roots, tuple(self._extra_paths()))
        result = replace(result, games=tuple(self._with_artwork(game) for game in result.games))
        with self._lock:
            ready = self._ready_artwork
            self._ready_artwork = {}
            self._scan = replace(
                result,
                games=tuple(
                    _apply_artwork(game, ready[game.appid]) if game.image_pending and game.appid in ready else game
                    for game in result.games
                ),
            )
            self._scanned = True
            self._read_settings_locked()
            snapshot = self._snapshot_locked()
        self._emit(snapshot)
        return snapshot

    def refresh_async(self) -> None:
        """Rescan on a worker thread; requests during a scan coalesce into one rerun."""
        with self._lock:
            if self._scan_running:
                self._scan_pending = True
                return
            self._scan_running = True
        threading.Thread(target=self._scan_worker, name="jugoo-steam-scan", daemon=True).start()

    def ignore(self, appid: str) -> None:
        ident = normalize_steam_appid(appid)
        if ident:
            with self._lock:
                ignored = self._ignored
            self._set_ignored(ignored + (ident,))

    def unignore(self, appid: str) -> None:
        """Drops the appid from ``steam-prefs.json``; one listed in the setting stays ignored."""
        ident = normalize_steam_appid(appid)
        if ident:
            with self._lock:
                ignored = self._ignored
            self._set_ignored(tuple(item for item in ignored if item != ident))

    def toggle_ignore(self, appid: str) -> None:
        if self.snapshot.is_ignored(appid):
            self.unignore(appid)
        else:
            self.ignore(appid)

    def set_include_ignored(self, include: bool) -> None:
        with self._lock:
            if bool(include) == self._include_ignored:
                return
            self._include_ignored = bool(include)
            snapshot = self._snapshot_locked()
        self._emit(snapshot)

    def _scan_worker(self) -> None:
        while True:
            try:
                self.refresh()
            except Exception as error:
                print(f"shell: steam: scan failed: {error}")
            with self._lock:
                if not self._scan_pending:
                    self._scan_running = False
                    return
                self._scan_pending = False

    def _with_artwork(self, game: SteamGame) -> SteamGame:
        resolution = self._artwork.resolve(game.appid, self._on_artwork_ready)
        artwork = resolution.artwork
        return replace(
            game,
            image_path=artwork.path if artwork else "",
            image_kind=artwork.kind if artwork else "",
            image_pending=resolution.pending,
        )

    def _on_artwork_ready(self, appid: str, artwork: SteamArtwork | None) -> None:
        """Runs on a download thread. ``None`` keeps the interim image and clears the spinner."""
        with self._lock:
            self._ready_artwork[appid] = artwork
            current = next((game for game in self._scan.games if game.appid == appid), None)
            if current is None:
                return
            updated = _apply_artwork(current, artwork)
            self._scan = replace(
                self._scan,
                games=tuple(updated if game.appid == appid else game for game in self._scan.games),
            )
        self._event_bus.emit(STEAM_GAME_ARTWORK_CHANGED, updated)

    def _set_ignored(self, ignored: tuple[str, ...]) -> None:
        unique = unique_appids(ignored)
        with self._lock:
            if unique == self._ignored:
                return
            self._ignored = unique
            snapshot = self._snapshot_locked()
        save_ignored_appids(self._prefs_path, unique)
        self._emit(snapshot)

    def _read_settings_locked(self) -> None:
        self._settings_ignored = tuple(self._configured_ignored())
        self._settings_show_ignored = bool(self._configured_show_ignored())

    def _effective_ignored_locked(self) -> tuple[str, ...]:
        return unique_appids(self._ignored + self._settings_ignored)

    def _visible_games_locked(self) -> tuple[SteamGame, ...]:
        ignored = set(self._effective_ignored_locked())
        if self._include_ignored or self._settings_show_ignored or not ignored:
            return self._scan.games
        return tuple(game for game in self._scan.games if game.appid not in ignored)

    def _snapshot_locked(self) -> SteamCatalogSnapshot:
        return SteamCatalogSnapshot(
            games=self._visible_games_locked(),
            libraries=tuple(str(path) for path in self._scan.libraries),
            unavailable_libraries=tuple(str(path) for path in self._scan.unavailable),
            steam_found=self._scan.steam_found,
            ignored_appids=self._effective_ignored_locked(),
            include_ignored=self._include_ignored or self._settings_show_ignored,
            scanned=self._scanned,
        )

    def _on_refresh_requested(self, _payload: object) -> None:
        self.refresh_async()

    def _on_ignore_toggle(self, appid: object) -> None:
        if isinstance(appid, (str, int)):
            self.toggle_ignore(str(appid))

    def _on_show_ignored_toggle(self, value: object) -> None:
        if isinstance(value, bool):
            self.set_include_ignored(value)

    def _emit(self, snapshot: SteamCatalogSnapshot) -> None:
        self._event_bus.emit(STEAM_GAMES_CHANGED, snapshot)
