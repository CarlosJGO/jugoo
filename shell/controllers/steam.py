"""Coordinates the Steam games panel with the catalog service and other overlays."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ..eventbus import EventBus
from ..models import SteamCatalogSnapshot, SteamGame
from ..popup_handle import PopupHandle
from ..servicios.steam.launch import (
    STEAM_GAME_LAUNCH_FAILED,
    STEAM_GAME_LAUNCH_REQUESTED,
    SteamLaunchOutcome,
    failure_message,
)
from ..servicios.steam.service import (
    STEAM_GAME_ARTWORK_CHANGED,
    STEAM_GAMES_CHANGED,
    SteamGamesService,
)
from ..widgets.juegos.steam_panel import SteamGamesPanel

Notifier = Callable[[str, str], None]


class SteamController:
    """Opening the panel closes other overlays and rescans libraries off the GTK thread.

    Choosing a game asks the launcher (via the bus) and closes the panel right
    away; a failed launch comes back as a Jugoo notification.
    """

    def __init__(
        self,
        event_bus: EventBus,
        steam: SteamGamesService,
        shell_window: Gtk.Window,
        *,
        close_other_overlays: Callable[[], None],
        notify: Notifier | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._steam = steam
        self._close_other_overlays = close_other_overlays
        self._notify = notify
        self._panel = PopupHandle(
            lambda: SteamGamesPanel(shell_window, on_activate=self._on_game_activated)
        )
        event_bus.subscribe(STEAM_GAMES_CHANGED, self._on_games_changed)
        event_bus.subscribe(STEAM_GAME_ARTWORK_CHANGED, self._on_artwork_changed)
        event_bus.subscribe(STEAM_GAME_LAUNCH_FAILED, self._on_launch_failed)

    def toggle(self) -> None:
        panel = self._panel.get()
        if panel.is_effectively_open():
            panel.close_panel()
            return
        self._close_other_overlays()
        panel.set_snapshot(self._steam.snapshot)
        panel.open_panel()
        self._steam.refresh_async()

    def close_panel(self) -> None:
        panel = self._panel.maybe
        if panel is not None:
            panel.close_panel()

    def close(self) -> None:
        self._event_bus.unsubscribe(STEAM_GAMES_CHANGED, self._on_games_changed)
        self._event_bus.unsubscribe(STEAM_GAME_ARTWORK_CHANGED, self._on_artwork_changed)
        self._event_bus.unsubscribe(STEAM_GAME_LAUNCH_FAILED, self._on_launch_failed)
        panel = self._panel.maybe
        if panel is not None:
            panel.dismiss_immediately()

    def _on_games_changed(self, snapshot: object) -> None:
        panel = self._panel.maybe
        if panel is not None and isinstance(snapshot, SteamCatalogSnapshot):
            panel.set_snapshot(snapshot)

    def _on_game_activated(self, game: SteamGame) -> None:
        self._event_bus.emit(STEAM_GAME_LAUNCH_REQUESTED, game)
        self.close_panel()

    def _on_launch_failed(self, outcome: object) -> None:
        if isinstance(outcome, SteamLaunchOutcome) and self._notify is not None:
            self._notify("Steam", failure_message(outcome))

    def _on_artwork_changed(self, game: object) -> None:
        panel = self._panel.maybe
        if panel is not None and isinstance(game, SteamGame):
            panel.update_game(game)
