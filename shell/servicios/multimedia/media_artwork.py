"""Background artwork fetch/cache for MPRIS metadata."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from pathlib import Path

from ...config import MEDIA_ARTWORK_DOWNLOAD_TIMEOUT_SEC
from ...runtime_paths import media_artwork_dir

_logger = logging.getLogger(__name__)
_MAX_WORKERS = 2
_MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
_MAX_CACHE_BYTES = 64 * 1024 * 1024
_MAX_CACHE_FILES = 128
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",  # WebP is checked further below.
)


class MediaArtworkCache:
    """Downloads and caches album art without blocking the GTK thread."""

    def __init__(self, cache_root: Path | None = None) -> None:
        self._cache_root = cache_root or media_artwork_dir()
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=_MAX_WORKERS,
            thread_name_prefix="media-artwork",
        )
        self._inflight: dict[str, list[Callable[[str, str], None]]] = {}
        self._futures: set[Future[None]] = set()
        self._memory_paths: dict[str, str] = {}
        self._closed = False

    def resolve(
        self,
        art_url: str,
        *,
        on_ready: Callable[[str, str], None] | None = None,
    ) -> str:
        """Return a cached path if available; schedule download otherwise."""
        normalized = art_url.strip()
        if not normalized:
            return ""

        with self._lock:
            if self._closed:
                return ""
            cached = self._memory_paths.get(normalized)
            if cached and Path(cached).is_file():
                return cached

        path = self._cache_path_for_url(normalized)
        if path.is_file():
            self._touch(path)
            with self._lock:
                self._memory_paths[normalized] = str(path)
            return str(path)

        if on_ready is not None:
            self._schedule_download(normalized, path, on_ready)
        return ""

    def _cache_path_for_url(self, art_url: str) -> Path:
        digest = hashlib.sha256(art_url.encode("utf-8")).hexdigest()
        suffix = _suffix_for_url(art_url)
        return self._cache_root / f"{digest}{suffix}"

    def _schedule_download(
        self,
        art_url: str,
        target: Path,
        on_ready: Callable[[str, str], None],
    ) -> None:
        with self._lock:
            if self._closed:
                return
            callbacks = self._inflight.get(art_url)
            if callbacks is not None:
                callbacks.append(on_ready)
                return
            self._inflight[art_url] = [on_ready]

        def _worker() -> None:
            downloaded = self._download_to_cache(art_url, target)
            with self._lock:
                callbacks = self._inflight.pop(art_url, [])
                closed = self._closed
                if downloaded and not closed:
                    self._memory_paths[art_url] = downloaded
            if downloaded and not closed:
                for callback in callbacks:
                    callback(art_url, downloaded)

        future = self._executor.submit(_worker)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def close(self) -> None:
        """Cancel queued downloads and suppress callbacks from active workers."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._inflight.clear()
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _discard_future(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)

    def _download_to_cache(self, art_url: str, target: Path) -> str:
        try:
            self._cache_root.mkdir(parents=True, exist_ok=True)
            if art_url.startswith("file://"):
                source = Path(urllib.parse.unquote(urllib.parse.urlparse(art_url).path))
                if source.is_file() and source.stat().st_size <= _MAX_DOWNLOAD_BYTES:
                    return self._copy_image_to_cache(source, target)
                return ""
            if art_url.startswith(("http://", "https://")):
                request = urllib.request.Request(
                    art_url,
                    headers={"User-Agent": "ShellMediaService/1.0"},
                )
                with urllib.request.urlopen(
                    request,
                    timeout=MEDIA_ARTWORK_DOWNLOAD_TIMEOUT_SEC,
                ) as response:
                    content_type = response.headers.get_content_type().lower()
                    if not content_type.startswith("image/"):
                        return ""
                    data = response.read(_MAX_DOWNLOAD_BYTES + 1)
                if not _is_image_data(data) or len(data) > _MAX_DOWNLOAD_BYTES:
                    return ""
                self._write_atomic(target, data)
                self._cleanup_cache()
                return str(target)
        except (OSError, ValueError, urllib.error.URLError) as error:
            _logger.debug("Artwork download unavailable for %s: %s", art_url, error)
            return ""
        except Exception as error:
            _logger.warning("Artwork download failed for %s: %s", art_url, error)
            return ""
        return ""

    def _copy_image_to_cache(self, source: Path, target: Path) -> str:
        try:
            data = source.read_bytes()
        except OSError:
            return ""
        if not _is_image_data(data):
            return ""
        self._write_atomic(target, data)
        self._cleanup_cache()
        return str(target)

    @staticmethod
    def _write_atomic(target: Path, data: bytes) -> None:
        temporary = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _touch(path: Path) -> None:
        try:
            path.touch(exist_ok=True)
        except OSError:
            return

    def _cleanup_cache(self) -> None:
        try:
            entries = [path for path in self._cache_root.iterdir() if path.is_file()]
            entries.sort(key=lambda path: path.stat().st_mtime)
            total = sum(path.stat().st_size for path in entries)
            while entries and (len(entries) > _MAX_CACHE_FILES or total > _MAX_CACHE_BYTES):
                path = entries.pop(0)
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
        except OSError as error:
            _logger.debug("Artwork cache cleanup skipped: %s", error)


def _is_image_data(data: bytes) -> bool:
    if not data or not any(data.startswith(signature) for signature in _IMAGE_SIGNATURES):
        return False
    return not data.startswith(b"RIFF") or data[8:12] == b"WEBP"


def _suffix_for_url(art_url: str) -> str:
    if art_url.startswith("file://"):
        path = Path(urllib.parse.unquote(urllib.parse.urlparse(art_url).path))
        return path.suffix.lower() if path.suffix else ".img"
    parsed = urllib.parse.urlparse(art_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return ".jpg"
