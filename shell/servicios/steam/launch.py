"""Start Steam games through the ``steam://`` URI handler, off the GTK thread.

Kept apart from the panel: anything that emits ``STEAM_GAME_LAUNCH_REQUESTED``
can launch a game, and the outcome comes back as events.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import shutil
import subprocess
import threading
import time

import gi

gi.require_version("Gio", "2.0")

from gi.repository import Gio, GLib

from ...eventbus import EventBus
from ...models import SteamGame, normalize_steam_appid

# Payload: the ``SteamGame`` to start (Enter or click in the panel).
STEAM_GAME_LAUNCH_REQUESTED = "steam_game_launch_requested"
# Payload: ``SteamLaunchOutcome``.
STEAM_GAME_LAUNCHED = "steam_game_launched"
STEAM_GAME_LAUNCH_FAILED = "steam_game_launch_failed"

FAILURE_STEAM_MISSING = "steam_missing"
FAILURE_LAUNCH_ERROR = "launch_error"

# A second Enter/click on the same game inside this window is ignored.
REPEAT_GUARD_SEC = 3.0
# uwsm/steam either fail fast or hand the URI to the running client and exit.
UWSM_WAIT_SEC = 2.0


@dataclass(frozen=True)
class SteamLaunchOutcome:
    game: SteamGame
    uri: str
    ok: bool
    reason: str = ""
    detail: str = ""


def steam_launch_uri(appid: str) -> str:
    normalized = normalize_steam_appid(appid)
    if not normalized:
        raise ValueError(f"invalid Steam appid: {appid!r}")
    return f"steam://rungameid/{normalized}"


def failure_message(outcome: SteamLaunchOutcome) -> str:
    """User-facing text for a failed launch (Spanish, like the rest of the UI)."""
    if outcome.reason == FAILURE_STEAM_MISSING:
        return f"No se pudo abrir {outcome.game.name}: Steam no está instalado o no abre enlaces steam://."
    detail = f" ({outcome.detail})" if outcome.detail else ""
    return f"No se pudo abrir {outcome.game.name}{detail}."


def default_handler_id() -> str | None:
    """Desktop id of the ``steam://`` handler, or None when nothing handles it."""
    handler = Gio.AppInfo.get_default_for_uri_scheme("steam")
    if handler is None:
        return None
    return handler.get_id() or ""


def _run_uwsm(handler_id: str, uri: str) -> str | None:
    """Launch in its own systemd scope like the app launcher. None on success, else the error."""
    if not handler_id or not shutil.which("uwsm"):
        return "uwsm unavailable"
    try:
        # No stderr pipe: if Steam was not running, this process becomes the
        # Steam client and would block once an unread pipe filled up.
        process = subprocess.Popen(
            ["uwsm", "app", "--", handler_id, uri],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        return str(error)
    try:
        code = process.wait(timeout=UWSM_WAIT_SEC)
    except subprocess.TimeoutExpired:
        return None
    return None if code == 0 else f"uwsm exited with {code}"


def _open_with_gio(uri: str) -> str | None:
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as error:
        return error.message
    return None


class SteamGameLauncher:
    """Listens for launch requests and reports success or failure on the bus."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        resolve_handler: Callable[[], str | None] = default_handler_id,
        openers: Sequence[Callable[[str, str], str | None]] | None = None,
        run_async: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._event_bus = event_bus
        self._resolve_handler = resolve_handler
        self._openers = tuple(openers) if openers is not None else (
            _run_uwsm,
            lambda _handler, uri: _open_with_gio(uri),
        )
        self._run_async = run_async or self._spawn_thread
        self._clock = clock
        self._lock = threading.Lock()
        self._last_launch: dict[str, float] = {}
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._event_bus.subscribe(STEAM_GAME_LAUNCH_REQUESTED, self._on_launch_requested)

    def close(self) -> None:
        if not self._started:
            return
        self._started = False
        self._event_bus.unsubscribe(STEAM_GAME_LAUNCH_REQUESTED, self._on_launch_requested)

    def launch(self, game: SteamGame) -> bool:
        """Queue a launch; False when the appid is invalid or was just launched."""
        try:
            uri = steam_launch_uri(game.appid)
        except ValueError as error:
            print(f"shell: steam: {error}")
            return False
        now = self._clock()
        with self._lock:
            last = self._last_launch.get(game.appid)
            if last is not None and now - last < REPEAT_GUARD_SEC:
                return False
            self._last_launch[game.appid] = now
        self._run_async(lambda: self._launch_blocking(game, uri))
        return True

    def _launch_blocking(self, game: SteamGame, uri: str) -> None:
        handler = self._resolve_handler()
        if handler is None:
            self._report(SteamLaunchOutcome(game, uri, ok=False, reason=FAILURE_STEAM_MISSING))
            return
        errors: list[str] = []
        for opener in self._openers:
            error = opener(handler, uri)
            if error is None:
                print(f"shell: steam: launched {game.appid} ({game.name})")
                self._report(SteamLaunchOutcome(game, uri, ok=True))
                return
            errors.append(error)
        detail = errors[-1] if errors else ""
        print(f"shell: steam: could not launch {game.appid}: {'; '.join(errors)}")
        self._report(SteamLaunchOutcome(game, uri, ok=False, reason=FAILURE_LAUNCH_ERROR, detail=detail))

    def _report(self, outcome: SteamLaunchOutcome) -> None:
        if not outcome.ok:
            with self._lock:
                self._last_launch.pop(outcome.game.appid, None)
        self._event_bus.emit(STEAM_GAME_LAUNCHED if outcome.ok else STEAM_GAME_LAUNCH_FAILED, outcome)

    def _on_launch_requested(self, game: object) -> None:
        if isinstance(game, SteamGame):
            self.launch(game)

    @staticmethod
    def _spawn_thread(work: Callable[[], None]) -> None:
        threading.Thread(target=work, name="jugoo-steam-launch", daemon=True).start()
