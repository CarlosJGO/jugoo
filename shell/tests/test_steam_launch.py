from __future__ import annotations

from shell.eventbus import EventBus
from shell.models import SteamGame
from shell.servicios.steam.launch import (
    FAILURE_LAUNCH_ERROR,
    FAILURE_STEAM_MISSING,
    REPEAT_GUARD_SEC,
    STEAM_GAME_LAUNCH_FAILED,
    STEAM_GAME_LAUNCH_REQUESTED,
    STEAM_GAME_LAUNCHED,
    SteamGameLauncher,
    SteamLaunchOutcome,
    failure_message,
    steam_launch_uri,
)

GAME = SteamGame(appid="312520", name="Rain World", library_path="/mnt/Almighty/SteamLibrary")


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _launcher(bus: EventBus, *, handler: str | None = "steam.desktop", openers=None, clock=None):
    calls: list[tuple[str, str]] = []

    def ok(handler_id: str, uri: str) -> None:
        calls.append((handler_id, uri))
        return None

    launcher = SteamGameLauncher(
        bus,
        resolve_handler=lambda: handler,
        openers=openers if openers is not None else (ok,),
        run_async=lambda work: work(),
        clock=clock or _Clock(),
    )
    return launcher, calls


def _collect(bus: EventBus) -> dict[str, list[SteamLaunchOutcome]]:
    seen: dict[str, list[SteamLaunchOutcome]] = {STEAM_GAME_LAUNCHED: [], STEAM_GAME_LAUNCH_FAILED: []}
    for name in seen:
        bus.subscribe(name, lambda outcome, name=name: seen[name].append(outcome))
    return seen


def test_launch_uri_uses_rungameid() -> None:
    assert steam_launch_uri("312520") == "steam://rungameid/312520"
    assert steam_launch_uri(" 40800 ") == "steam://rungameid/40800"
    for bad in ("", "abc", "12;rm -rf"):
        try:
            steam_launch_uri(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_launch_request_event_opens_uri_with_the_handler() -> None:
    bus = EventBus()
    seen = _collect(bus)
    launcher, calls = _launcher(bus)
    launcher.start()
    bus.emit(STEAM_GAME_LAUNCH_REQUESTED, GAME)
    assert calls == [("steam.desktop", "steam://rungameid/312520")]
    assert [outcome.ok for outcome in seen[STEAM_GAME_LAUNCHED]] == [True]
    assert seen[STEAM_GAME_LAUNCH_FAILED] == []
    launcher.close()
    bus.emit(STEAM_GAME_LAUNCH_REQUESTED, GAME)
    assert len(calls) == 1


def test_missing_steam_handler_reports_failure_without_opening() -> None:
    bus = EventBus()
    seen = _collect(bus)
    launcher, calls = _launcher(bus, handler=None)
    assert launcher.launch(GAME)
    assert calls == []
    (outcome,) = seen[STEAM_GAME_LAUNCH_FAILED]
    assert outcome.reason == FAILURE_STEAM_MISSING
    assert "Steam no está instalado" in failure_message(outcome)


def test_falls_back_to_next_opener_and_reports_last_error() -> None:
    bus = EventBus()
    seen = _collect(bus)
    order: list[str] = []

    def uwsm(_handler: str, _uri: str) -> str:
        order.append("uwsm")
        return "uwsm exited with 1"

    def gio(_handler: str, _uri: str) -> None:
        order.append("gio")
        return None

    launcher, _calls = _launcher(bus, openers=(uwsm, gio))
    launcher.launch(GAME)
    assert order == ["uwsm", "gio"]
    assert len(seen[STEAM_GAME_LAUNCHED]) == 1

    def broken(_handler: str, _uri: str) -> str:
        return "Operation not supported"

    other = SteamGame(appid="40800", name="Super Meat Boy", library_path="/lib")
    launcher_fail, _ = _launcher(bus, openers=(uwsm, broken))
    launcher_fail.launch(other)
    (outcome,) = seen[STEAM_GAME_LAUNCH_FAILED]
    assert outcome.reason == FAILURE_LAUNCH_ERROR
    assert failure_message(outcome) == "No se pudo abrir Super Meat Boy (Operation not supported)."


def test_repeat_activation_is_ignored_briefly_but_failures_can_retry() -> None:
    bus = EventBus()
    clock = _Clock()
    launcher, calls = _launcher(bus, clock=clock)
    assert launcher.launch(GAME)
    assert not launcher.launch(GAME)
    clock.now += REPEAT_GUARD_SEC + 0.1
    assert launcher.launch(GAME)
    assert len(calls) == 2

    failing, _ = _launcher(bus, handler=None, clock=clock)
    assert failing.launch(GAME)
    assert failing.launch(GAME)


def test_launch_runs_off_the_calling_thread_by_default() -> None:
    import threading

    bus = EventBus()
    started = threading.Event()
    release = threading.Event()
    threads: list[str] = []

    def slow(_handler: str, _uri: str) -> None:
        threads.append(threading.current_thread().name)
        started.set()
        release.wait(2)
        return None

    launcher = SteamGameLauncher(bus, resolve_handler=lambda: "steam.desktop", openers=(slow,))
    assert launcher.launch(GAME)
    assert started.wait(2)
    release.set()
    assert threads == ["jugoo-steam-launch"]


def test_controller_requests_launch_and_notifies_failures() -> None:
    from shell.controllers.steam import SteamController

    bus = EventBus()
    requested: list[str] = []
    notes: list[tuple[str, str]] = []
    bus.subscribe(STEAM_GAME_LAUNCH_REQUESTED, lambda game: requested.append(game.appid))
    controller = SteamController(
        bus,
        steam=None,
        shell_window=None,
        close_other_overlays=lambda: None,
        notify=lambda summary, body: notes.append((summary, body)),
    )
    try:
        controller._on_game_activated(GAME)
        assert requested == ["312520"]
        bus.emit(
            STEAM_GAME_LAUNCH_FAILED,
            SteamLaunchOutcome(GAME, steam_launch_uri(GAME.appid), ok=False, reason=FAILURE_STEAM_MISSING),
        )
        assert notes == [("Steam", failure_message(SteamLaunchOutcome(GAME, "", ok=False, reason=FAILURE_STEAM_MISSING)))]
        bus.emit(STEAM_GAME_LAUNCHED, SteamLaunchOutcome(GAME, "", ok=True))
        assert len(notes) == 1
    finally:
        controller.close()


def _run() -> None:
    import inspect

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        assert not inspect.signature(test).parameters
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
