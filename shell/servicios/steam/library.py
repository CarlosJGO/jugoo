"""Discover Steam libraries and read installed games from ``appmanifest_*.acf``."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable

from ...models import SteamGame, normalize_steam_appid
from .vdf import VdfError, parse_vdf, vdf_get

# Relative to $HOME. ``~/.steam/steam`` is usually a symlink to the first one;
# the duplicates collapse when paths are resolved.
STEAM_ROOT_CANDIDATES = (
    ".local/share/Steam",
    ".steam/steam",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ".var/app/com.valvesoftware.Steam/data/Steam",
)

# ``StateFlags`` bits from Steam's EAppState.
STATE_UNINSTALLED = 1 << 0
STATE_UPDATE_REQUIRED = 1 << 1
STATE_FULLY_INSTALLED = 1 << 2
STATE_FILES_MISSING = 1 << 5
STATE_APP_RUNNING = 1 << 6
STATE_FILES_CORRUPT = 1 << 7
STATE_UPDATE_RUNNING = 1 << 8
STATE_UPDATE_PAUSED = 1 << 9
STATE_UPDATE_STARTED = 1 << 10
STATE_UNINSTALLING = 1 << 11
STATE_RECONFIGURING = 1 << 16
STATE_VALIDATING = 1 << 17
STATE_ADDING_FILES = 1 << 18
STATE_PREALLOCATING = 1 << 19
STATE_DOWNLOADING = 1 << 20
STATE_STAGING = 1 << 21
STATE_COMMITTING = 1 << 22
STATE_UPDATE_STOPPING = 1 << 23

# Any of these means Steam is still writing, removing or repairing the files.
# A queued update (UPDATE_REQUIRED alone) keeps the game listed.
_STATE_NOT_READY = (
    STATE_UNINSTALLED
    | STATE_FILES_MISSING
    | STATE_FILES_CORRUPT
    | STATE_UPDATE_RUNNING
    | STATE_UPDATE_PAUSED
    | STATE_UPDATE_STARTED
    | STATE_UNINSTALLING
    | STATE_RECONFIGURING
    | STATE_VALIDATING
    | STATE_ADDING_FILES
    | STATE_PREALLOCATING
    | STATE_DOWNLOADING
    | STATE_STAGING
    | STATE_COMMITTING
    | STATE_UPDATE_STOPPING
)

# Tools Steam installs next to games. The user hides anything else with Ignore.
_NON_GAME_APPIDS = frozenset(
    {
        "228980",  # Steamworks Common Redistributables
        "1070560",  # Steam Linux Runtime 1.0 (scout)
        "1391110",  # Steam Linux Runtime 2.0 (soldier)
        "1628350",  # Steam Linux Runtime 3.0 (sniper)
        "4183110",  # Steam Linux Runtime 4.0
        "1493710",  # Proton Experimental
        "2180100",  # Proton Hotfix
        "1161040",  # Proton BattlEye Runtime
        "1826330",  # Proton EasyAntiCheat Runtime
    }
)
_NON_GAME_NAME = re.compile(
    r"^(?:"
    r"proton (?:\d|experimental\b|hotfix\b|next\b|easyanticheat\b|battleye\b)"
    r"|steam linux runtime\b"
    r"|steamworks\b"
    r"|steam runtime\b"
    r")",
    re.IGNORECASE,
)

_LIBRARY_PATH_SEPARATORS = re.compile(r"[;:\n]")


@dataclass(frozen=True)
class SteamLibraryDiscovery:
    """Readable library roots (each has ``steamapps/``) and the ones that were not."""

    libraries: tuple[Path, ...] = ()
    unavailable: tuple[Path, ...] = ()
    steam_found: bool = False


@dataclass(frozen=True)
class SteamScanResult:
    games: tuple[SteamGame, ...] = ()
    libraries: tuple[Path, ...] = ()
    unavailable: tuple[Path, ...] = ()
    steam_found: bool = False


def steam_root_candidates(home: Path | None = None) -> tuple[Path, ...]:
    base = home if home is not None else Path.home()
    return tuple(base / relative for relative in STEAM_ROOT_CANDIDATES)


def parse_library_paths(value: object) -> tuple[Path, ...]:
    """Split the user setting into paths; ``~`` is expanded, blanks dropped."""
    if not isinstance(value, str):
        return ()
    paths: list[Path] = []
    for part in _LIBRARY_PATH_SEPARATORS.split(value):
        text = part.strip()
        if text:
            paths.append(Path(os.path.expanduser(text)))
    return tuple(paths)


def read_library_folders(path: Path) -> tuple[Path, ...]:
    """Library paths listed in ``libraryfolders.vdf``; empty when unreadable."""
    try:
        data = parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, VdfError) as error:
        print(f"shell: steam: could not read {path}: {error}")
        return ()
    folders = vdf_get(data, "libraryfolders")
    if not isinstance(folders, dict):
        print(f"shell: steam: {path} has no libraryfolders section")
        return ()
    paths: list[Path] = []
    for key, entry in folders.items():
        # Old format: "1" "/path"; current format: "1" { "path" "/path" ... }.
        raw = vdf_get(entry, "path") if isinstance(entry, dict) else entry
        if isinstance(raw, str) and raw.strip() and key.strip().isdigit():
            paths.append(Path(raw.strip()))
    return tuple(paths)


def discover_steam_libraries(
    roots: Iterable[Path] | None = None,
    extra_paths: Iterable[Path] = (),
) -> SteamLibraryDiscovery:
    """Steam roots first, then ``libraryfolders.vdf`` entries, then user extras."""
    candidates: list[Path] = []
    steam_found = False
    for root in roots if roots is not None else steam_root_candidates():
        if not _is_dir(root / "steamapps"):
            continue
        steam_found = True
        candidates.append(root)
        for vdf_path in (root / "steamapps" / "libraryfolders.vdf", root / "config" / "libraryfolders.vdf"):
            if _is_file(vdf_path):
                candidates.extend(read_library_folders(vdf_path))
                break
    for extra in extra_paths:
        candidates.append(extra.parent if extra.name == "steamapps" else extra)

    libraries: list[Path] = []
    unavailable: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _path_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _is_dir(candidate / "steamapps"):
            libraries.append(candidate)
        else:
            unavailable.append(candidate)
    if unavailable:
        listed = ", ".join(str(item) for item in unavailable)
        print(f"shell: steam: skipping unavailable libraries: {listed}")
    return SteamLibraryDiscovery(tuple(libraries), tuple(unavailable), steam_found)


def is_fully_installed(state_flags: int) -> bool:
    return bool(state_flags & STATE_FULLY_INSTALLED) and not state_flags & _STATE_NOT_READY


def is_non_game(appid: str, name: str) -> bool:
    return appid in _NON_GAME_APPIDS or bool(_NON_GAME_NAME.match(name.strip()))


def read_app_manifest(path: Path, library: Path) -> SteamGame | None:
    """One installed game, or ``None`` for broken, incomplete or not-ready manifests."""
    try:
        data = parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, VdfError) as error:
        print(f"shell: steam: invalid manifest {path}: {error}")
        return None
    state = vdf_get(data, "AppState")
    if not isinstance(state, dict):
        print(f"shell: steam: manifest {path} has no AppState")
        return None
    appid = normalize_steam_appid(vdf_get(state, "appid"))
    if not appid:
        print(f"shell: steam: manifest {path} has no valid appid")
        return None
    flags_text = vdf_get(state, "StateFlags")
    if not isinstance(flags_text, str) or not flags_text.strip().isdigit():
        print(f"shell: steam: manifest {path} has no StateFlags")
        return None
    if not is_fully_installed(int(flags_text.strip())):
        return None
    install_dir = vdf_get(state, "installdir")
    install_dir = install_dir.strip() if isinstance(install_dir, str) else ""
    name = vdf_get(state, "name")
    name = name.strip() if isinstance(name, str) else ""
    name = name or install_dir
    if not name:
        print(f"shell: steam: manifest {path} has no name")
        return None
    return SteamGame(
        appid=appid,
        name=name,
        library_path=str(library),
        install_dir=install_dir,
    )


def scan_library_games(library: Path) -> tuple[SteamGame, ...]:
    steamapps = library / "steamapps"
    try:
        manifests = sorted(steamapps.glob("appmanifest_*.acf"))
    except OSError as error:
        print(f"shell: steam: could not list {steamapps}: {error}")
        return ()
    games: list[SteamGame] = []
    for manifest in manifests:
        game = read_app_manifest(manifest, library)
        if game is not None:
            games.append(game)
    return tuple(games)


def scan_steam_games(
    roots: Iterable[Path] | None = None,
    extra_paths: Iterable[Path] = (),
) -> SteamScanResult:
    """Installed games across every reachable library, unique by appid, A→Z."""
    discovery = discover_steam_libraries(roots, extra_paths)
    games: dict[str, SteamGame] = {}
    for library in discovery.libraries:
        for game in scan_library_games(library):
            if game.appid in games or is_non_game(game.appid, game.name):
                continue
            games[game.appid] = game
    ordered = tuple(sorted(games.values(), key=lambda game: (game.name.casefold(), game.appid)))
    return SteamScanResult(
        games=ordered,
        libraries=discovery.libraries,
        unavailable=discovery.unavailable,
        steam_found=discovery.steam_found,
    )


def _path_key(path: Path) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False
