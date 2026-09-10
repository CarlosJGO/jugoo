"""Clipboard history owned by the shell process. Uses wl-paste --watch, never polls.

History limits never interfere with the real system clipboard: oversized text is
skipped for persistence only, and image store failures are ignored for paste.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ...config import (
    CLIPBOARD_HISTORY_LIMIT,
    CLIPBOARD_MAX_HISTORY_BYTES,
    CLIPBOARD_MAX_TEXT_BYTES,
)
from ...eventbus import EventBus
from ...runtime_paths import clipboard_history_path, clipboard_images_dir, xdg_data_dir
from .historia import (
    ClipboardEntry,
    ClipboardHistory,
    hash_bytes,
    load_history_result,
    save_history,
    search_entries,
)

CLIPBOARD_CHANGED = "clipboard_changed"

IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/bmp",
)

PasteFn = Callable[[], str | None]
CopyFn = Callable[[str], bool]
WatchFactory = Callable[[], subprocess.Popen[str]]
ListTypesFn = Callable[[], tuple[str, ...]]
PasteBytesFn = Callable[[str], bytes | None]
CopyBytesFn = Callable[[bytes, str], bool]


def paste_text_to_window(
    address: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Focus the previous window and send Ctrl+V without blocking GTK."""
    if not address.strip():
        return False
    escaped_address = address.strip().replace("\\", "\\\\").replace('"', '\\"')
    try:
        focused = runner(
            [
                "hyprctl",
                "dispatch",
                f'hl.dsp.focus({{ window = "address:{escaped_address}" }})',
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if focused.returncode != 0:
            return False
        time.sleep(0.15)
        pasted = runner(
            ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return pasted.returncode == 0


def paste_text(*, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str | None:
    try:
        completed = runner(
            ["wl-paste", "--type", "text", "--no-newline"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def list_clipboard_types(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str, ...]:
    try:
        completed = runner(
            ["wl-paste", "--list-types"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode != 0:
        return ()
    return tuple(
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if line.strip()
    )


def paste_bytes(
    mime: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> bytes | None:
    try:
        completed = runner(
            ["wl-paste", "--type", mime],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    data = completed.stdout or b""
    return data or None


def copy_text(
    text: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    try:
        completed = runner(
            ["wl-copy", "--type", "text/plain"],
            input=text,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # wl-copy may keep a serving process alive; data is usually already offered.
        return True
    except OSError:
        return False
    return completed.returncode == 0


def copy_image_bytes(
    payload: bytes,
    *,
    mime: str = "image/png",
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> bool:
    if not payload:
        return False
    try:
        completed = runner(
            ["wl-copy", "--type", mime],
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        return False
    return completed.returncode == 0


def preferred_image_mime(types: tuple[str, ...]) -> str | None:
    available = {item.casefold(): item for item in types}
    for mime in IMAGE_MIME_TYPES:
        if mime in available:
            return available[mime]
    return None


def normalize_image_to_png(payload: bytes, mime: str) -> bytes | None:
    """Return PNG bytes. Passthrough when already PNG; otherwise convert via GdkPixbuf."""
    if not payload:
        return None
    normalized_mime = (mime or "").casefold().strip()
    if normalized_mime in {"image/png", "png"} and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return payload
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
    except Exception:
        return payload if payload.startswith(b"\x89PNG\r\n\x1a\n") else None

    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(payload)
        loader.close()
    except GLib.Error:
        try:
            loader.close()
        except Exception:
            pass
        return None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    try:
        ok, buffer = pixbuf.save_to_bufferv("png", [], [])
    except GLib.Error:
        return None
    if not ok or not buffer:
        return None
    return bytes(buffer)


def _default_watch_factory() -> subprocess.Popen[str]:
    # Watch every clipboard change (text or image); ingest decides the kind.
    return subprocess.Popen(
        ["wl-paste", "--watch", "printf", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


class ClipboardService:
    """Captures clipboard changes inside the existing Jugoo process."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        path: Path | None = None,
        data_dir: Path | None = None,
        images_dir: Path | None = None,
        limit: int = CLIPBOARD_HISTORY_LIMIT,
        max_item_bytes: int | None = None,
        max_text_bytes: int | None = None,
        max_history_bytes: int = CLIPBOARD_MAX_HISTORY_BYTES,
        paster: PasteFn | None = None,
        copier: CopyFn | None = None,
        list_types: ListTypesFn | None = None,
        paste_image: PasteBytesFn | None = None,
        copy_image: CopyBytesFn | None = None,
        watch_factory: WatchFactory | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._event_bus = event_bus
        self._path = path if path is not None else clipboard_history_path()
        resolved_data = data_dir if data_dir is not None else (
            self._path.parent if path is not None else xdg_data_dir()
        )
        resolved_images = (
            images_dir if images_dir is not None else (
                resolved_data / "clipboard" / "images"
                if path is not None
                else clipboard_images_dir()
            )
        )
        text_limit = (
            max_text_bytes
            if max_text_bytes is not None
            else (max_item_bytes if max_item_bytes is not None else CLIPBOARD_MAX_TEXT_BYTES)
        )
        self._history = ClipboardHistory(
            limit=limit,
            max_text_bytes=text_limit,
            max_history_bytes=max_history_bytes,
            data_dir=resolved_data,
            images_dir=resolved_images,
        )
        self._paster = paster or paste_text
        self._copier = copier or copy_text
        self._list_types = list_types or list_clipboard_types
        self._paste_image = paste_image or paste_bytes
        self._copy_image = copy_image or (
            lambda payload, mime: copy_image_bytes(payload, mime=mime)
        )
        self._watch_factory = watch_factory or _default_watch_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None

    @property
    def entries(self) -> tuple[ClipboardEntry, ...]:
        with self._lock:
            return self._history.entries

    def start(self) -> None:
        with self._lock:
            loaded = load_history_result(self._path)
            self._history.replace_entries(loaded.entries)
            if loaded.trusted:
                self._history.cleanup_orphans()
            # Persist migrated v2 shape when we loaded legacy v1 content.
            if loaded.trusted and loaded.entries and loaded.version < 2:
                save_history(self._path, self._history.entries)
        self._ingest_current()
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="clipboard-watch",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        process = self._process
        self._process = None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def search(self, query: str) -> tuple[ClipboardEntry, ...]:
        with self._lock:
            return search_entries(self._history.entries, query)

    def entry_by_id(self, entry_id: str) -> ClipboardEntry | None:
        with self._lock:
            return self._history.entry_by_id(entry_id)

    def remember_text(self, text: str, *, now: float | None = None) -> bool:
        changed = False
        with self._lock:
            if self._history.remember_text(text, now=self._clock() if now is None else now):
                self._persist_unlocked()
                changed = True
                snapshot = self._history.entries
        if changed:
            self._event_bus.emit(CLIPBOARD_CHANGED, snapshot)
        return changed

    def remember_image(
        self,
        payload: bytes,
        *,
        mime: str = "image/png",
        now: float | None = None,
    ) -> bool:
        png = normalize_image_to_png(payload, mime)
        if png is None:
            return False
        changed = False
        with self._lock:
            if self._history.remember_image(
                png,
                mime="image/png",
                now=self._clock() if now is None else now,
                content_hash=hash_bytes(png),
            ):
                self._persist_unlocked()
                changed = True
                snapshot = self._history.entries
        if changed:
            self._event_bus.emit(CLIPBOARD_CHANGED, snapshot)
        return changed

    def remove_entry(self, entry_id: str) -> bool:
        changed = False
        with self._lock:
            if self._history.remove_entry(entry_id):
                self._persist_unlocked()
                changed = True
                snapshot = self._history.entries
        if changed:
            self._event_bus.emit(CLIPBOARD_CHANGED, snapshot)
        return changed

    def copy_entry(self, entry_id: str) -> bool:
        entry = self.entry_by_id(entry_id)
        if entry is None:
            return False
        if entry.is_image:
            absolute = self.image_absolute_path(entry)
            if absolute is None or not absolute.is_file():
                return False
            try:
                payload = absolute.read_bytes()
            except OSError:
                return False
            if not self._copy_image(payload, entry.mime or "image/png"):
                return False
            self.remember_image(payload, mime=entry.mime or "image/png")
            return True
        if not self._copier(entry.text):
            return False
        self.remember_text(entry.text)
        return True

    def image_absolute_path(self, entry: ClipboardEntry) -> Path | None:
        from .historia import resolve_image_path

        return resolve_image_path(entry.path, data_dir=self._history.data_dir)

    def _persist_unlocked(self) -> None:
        try:
            save_history(self._path, self._history.entries)
        except Exception:
            # Persistence must never break the real clipboard path.
            pass

    def _ingest_current(self) -> None:
        try:
            types = self._list_types()
        except Exception:
            types = ()
        image_mime = preferred_image_mime(types)
        if image_mime is not None:
            try:
                payload = self._paste_image(image_mime)
            except Exception:
                payload = None
            if payload:
                self.remember_image(payload, mime=image_mime)
                return
        try:
            text = self._paster()
        except Exception:
            text = None
        if text:
            self.remember_text(text)

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                process = self._watch_factory()
            except OSError:
                return
            self._process = process
            stdout = process.stdout
            if stdout is None:
                return
            try:
                while not self._stop_event.is_set():
                    chunk = stdout.read(1)
                    if chunk == "":
                        break
                    self._ingest_current()
            finally:
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except Exception:
                    pass
            if self._stop_event.wait(1.0):
                return
