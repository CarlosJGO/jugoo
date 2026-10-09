"""Resolve Steam game covers: Steam's librarycache, then Jugoo's cache, then the CDN."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Callable, Iterable
import urllib.error
import urllib.request

from ... import config as shell_config
from ...models import normalize_steam_appid
from ...runtime_paths import steam_artwork_dir
from .library import steam_root_candidates

ARTWORK_VERTICAL = "vertical"
ARTWORK_HEADER = "header"

SOURCE_STEAM = "steam"
SOURCE_CACHE = "cache"
SOURCE_NETWORK = "network"

# Steam keeps both layouts: ``<appid>/library_600x900.jpg`` and, for newer
# assets, ``<appid>/<hash>/library_capsule.jpg``. Very old clients used flat
# ``<appid>_library_600x900.jpg`` files next to the per-app folders.
_LOCAL_NAMES = {
    ARTWORK_VERTICAL: ("library_600x900_2x.jpg", "library_600x900.jpg", "library_capsule.jpg"),
    ARTWORK_HEADER: ("library_header.jpg", "header.jpg"),
}
_FLAT_SUFFIXES = {
    ARTWORK_VERTICAL: ("_library_600x900_2x.jpg", "_library_600x900.jpg"),
    ARTWORK_HEADER: ("_header.jpg",),
}
_CDN_URLS = {
    ARTWORK_VERTICAL: (
        "https://shared.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg",
        "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
    ),
    ARTWORK_HEADER: (
        "https://shared.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg",
        "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
    ),
}
_MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
_MAX_WORKERS = 2

Fetcher = Callable[[str, float], "bytes | None"]
Submit = Callable[[Callable[[], None]], object]


@dataclass(frozen=True)
class SteamArtwork:
    path: str
    kind: str
    source: str


@dataclass(frozen=True)
class SteamArtworkResolution:
    """Best image available right now; ``pending`` means a better one is downloading."""

    artwork: SteamArtwork | None = None
    pending: bool = False


def librarycache_dirs(roots: Iterable[Path] | None = None) -> tuple[Path, ...]:
    dirs: list[Path] = []
    seen: set[str] = set()
    for root in roots if roots is not None else steam_root_candidates():
        directory = root / "appcache" / "librarycache"
        try:
            if not directory.is_dir():
                continue
            key = os.path.realpath(directory)
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            dirs.append(directory)
    return tuple(dirs)


def find_librarycache_image(appid: str, kind: str, dirs: Iterable[Path]) -> Path | None:
    names = _LOCAL_NAMES[kind]
    for directory in dirs:
        app_dir = directory / appid
        for name in names:
            if _is_file(app_dir / name):
                return app_dir / name
        try:
            subdirs = sorted(item for item in app_dir.iterdir() if item.is_dir())
        except OSError:
            subdirs = []
        for name in names:
            for subdir in subdirs:
                if _is_file(subdir / name):
                    return subdir / name
        for suffix in _FLAT_SUFFIXES[kind]:
            flat = directory / f"{appid}{suffix}"
            if _is_file(flat):
                return flat
    return None


def cached_image_path(cache_dir: Path, appid: str, kind: str) -> Path:
    return cache_dir / f"{appid}_{kind}.jpg"


def placeholder_initials(name: str) -> str:
    """Up to two initials for the cover placeholder ("Rain World" → "RW")."""
    words = [word for word in name.replace("_", " ").replace("-", " ").split() if word[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[1][:1]).upper()


def download_image(url: str, timeout: float) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "JugooSteamArtwork/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not response.headers.get_content_type().lower().startswith("image/"):
                return None
            data = response.read(_MAX_DOWNLOAD_BYTES + 1)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if len(data) > _MAX_DOWNLOAD_BYTES or not is_image_data(data):
        return None
    return data


def is_image_data(data: bytes) -> bool:
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        return True
    return data.startswith(b"RIFF") and data[8:12] == b"WEBP"


class SteamArtworkCache:
    """Local lookups are synchronous and cheap; CDN downloads never run on the caller's thread."""

    def __init__(
        self,
        *,
        roots: Iterable[Path] | None = None,
        cache_dir: Path | None = None,
        fetcher: Fetcher | None = None,
        submit: Submit | None = None,
        network_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._roots = tuple(roots) if roots is not None else None
        self._cache_dir = cache_dir if cache_dir is not None else steam_artwork_dir()
        self._fetcher = fetcher or download_image
        self._network_enabled = network_enabled or (
            lambda: bool(getattr(shell_config, "STEAM_ARTWORK_DOWNLOAD_ENABLED", True))
        )
        self._executor: ThreadPoolExecutor | None = None
        self._submit = submit
        self._lock = threading.RLock()
        self._inflight: dict[str, list[Callable[[str, SteamArtwork | None], None]]] = {}
        # Appids the CDN could not serve this session; avoids retrying on every open.
        self._misses: set[str] = set()
        self._closed = False

    def resolve(
        self,
        appid: str,
        on_ready: Callable[[str, SteamArtwork | None], None] | None = None,
    ) -> SteamArtworkResolution:
        ident = normalize_steam_appid(appid)
        if not ident:
            return SteamArtworkResolution()
        dirs = librarycache_dirs(self._roots)
        vertical = self._local(ident, ARTWORK_VERTICAL, dirs)
        if vertical is not None:
            return SteamArtworkResolution(vertical)
        header = self._local(ident, ARTWORK_HEADER, dirs)
        if on_ready is not None and self._network_enabled() and self._schedule(ident, header is None, on_ready):
            return SteamArtworkResolution(header, pending=True)
        return SteamArtworkResolution(header)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._inflight.clear()
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _local(self, appid: str, kind: str, dirs: tuple[Path, ...]) -> SteamArtwork | None:
        found = find_librarycache_image(appid, kind, dirs)
        if found is not None:
            return SteamArtwork(str(found), kind, SOURCE_STEAM)
        cached = cached_image_path(self._cache_dir, appid, kind)
        if _is_file(cached):
            return SteamArtwork(str(cached), kind, SOURCE_CACHE)
        return None

    def _schedule(
        self,
        appid: str,
        need_header: bool,
        on_ready: Callable[[str, SteamArtwork | None], None],
    ) -> bool:
        with self._lock:
            if self._closed or appid in self._misses:
                return False
            callbacks = self._inflight.get(appid)
            if callbacks is not None:
                callbacks.append(on_ready)
                return True
            self._inflight[appid] = [on_ready]
            submit = self._submit or self._default_submit()
        submit(lambda: self._download_worker(appid, need_header))
        return True

    def _default_submit(self) -> Submit:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="steam-artwork")
        return self._executor.submit

    def _download_worker(self, appid: str, need_header: bool) -> None:
        artwork = self._download(appid, ARTWORK_VERTICAL)
        if artwork is None and need_header:
            artwork = self._download(appid, ARTWORK_HEADER)
        with self._lock:
            callbacks = self._inflight.pop(appid, [])
            if artwork is None:
                self._misses.add(appid)
            if self._closed:
                return
        for callback in callbacks:
            try:
                callback(appid, artwork)
            except Exception as error:
                print(f"shell: steam: artwork callback failed for {appid}: {error}")

    def _download(self, appid: str, kind: str) -> SteamArtwork | None:
        timeout = float(getattr(shell_config, "STEAM_ARTWORK_DOWNLOAD_TIMEOUT_SEC", 8))
        for template in _CDN_URLS[kind]:
            data = self._fetcher(template.format(appid=appid), timeout)
            if not data:
                continue
            target = cached_image_path(self._cache_dir, appid, kind)
            if _write_atomic(target, data):
                return SteamArtwork(str(target), kind, SOURCE_NETWORK)
            return None
        return None


def _write_atomic(target: Path, data: bytes) -> bool:
    temporary = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(data)
        temporary.replace(target)
        return True
    except OSError as error:
        print(f"shell: steam: could not cache {target}: {error}")
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False
