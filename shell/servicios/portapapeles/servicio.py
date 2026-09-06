"""Clipboard history owned by the shell process. Uses wl-paste --watch, never polls."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ...config import CLIPBOARD_HISTORY_LIMIT, CLIPBOARD_MAX_ITEM_BYTES
from ...eventbus import EventBus
from ...runtime_paths import clipboard_history_path
from .historia import ClipboardEntry, ClipboardHistory, load_history, save_history, search_entries

CLIPBOARD_CHANGED = "clipboard_changed"

PasteFn = Callable[[], str | None]
CopyFn = Callable[[str], bool]
WatchFactory = Callable[[], subprocess.Popen[str]]


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


def copy_text(
    text: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    try:
        completed = runner(
            ["wl-copy", "--type", "text/plain"],
            input=text,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _default_watch_factory() -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["wl-paste", "--type", "text", "--watch", "printf", "."],
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
        limit: int = CLIPBOARD_HISTORY_LIMIT,
        max_item_bytes: int = CLIPBOARD_MAX_ITEM_BYTES,
        paster: PasteFn | None = None,
        copier: CopyFn | None = None,
        watch_factory: WatchFactory | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._event_bus = event_bus
        self._path = path if path is not None else clipboard_history_path()
        self._history = ClipboardHistory(limit=limit, max_item_bytes=max_item_bytes)
        self._paster = paster or paste_text
        self._copier = copier or copy_text
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
            self._history.replace_entries(load_history(self._path))
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
            if self._history.remember(text, now=self._clock() if now is None else now):
                save_history(self._path, self._history.entries)
                changed = True
                snapshot = self._history.entries
        if changed:
            self._event_bus.emit(CLIPBOARD_CHANGED, snapshot)
        return changed

    def copy_entry(self, entry_id: str) -> bool:
        entry = self.entry_by_id(entry_id)
        if entry is None:
            return False
        if not self._copier(entry.text):
            return False
        self.remember_text(entry.text)
        return True

    def _ingest_current(self) -> None:
        text = self._paster()
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
