"""In-memory clipboard history with local JSON + image-file persistence.

Never logs payload text or image bytes. Limits only affect Jugoo history,
never the system clipboard.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

HISTORY_VERSION = 2
ENTRY_TEXT = "text"
ENTRY_IMAGE = "image"

DEFAULT_LIMIT = 200
DEFAULT_MAX_TEXT_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_HISTORY_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_ITEM_BYTES = DEFAULT_MAX_TEXT_BYTES  # alias
DEFAULT_PREVIEW_CHARS = 96
DEFAULT_PREVIEW_LINES = 2

IMAGE_REL_PREFIX = "clipboard/images"


@dataclass(frozen=True)
class ClipboardEntry:
    id: str
    text: str = ""
    copied_at: float = 0.0
    kind: str = ENTRY_TEXT
    mime: str = ""
    path: str = ""  # relative to XDG data dir, images only
    content_hash: str = ""

    @property
    def is_image(self) -> bool:
        return self.kind == ENTRY_IMAGE

    @property
    def is_text(self) -> bool:
        return self.kind != ENTRY_IMAGE


@dataclass(frozen=True)
class HistoryLoadResult:
    """Load outcome. ``trusted`` is False for corrupt JSON (skip orphan wipe)."""

    entries: tuple[ClipboardEntry, ...]
    trusted: bool
    version: int = 1


def preview_text(
    text: str,
    *,
    max_chars: int = DEFAULT_PREVIEW_CHARS,
    max_lines: int = DEFAULT_PREVIEW_LINES,
) -> str:
    """Visual truncation only. The stored payload stays intact."""
    lines = text.splitlines() or [""]
    visible = lines[: max(1, max_lines)]
    joined = " ⏎ ".join(visible)
    if len(lines) > max_lines:
        joined = f"{joined} ⏎ …"
    if len(joined) > max_chars:
        return joined[: max(1, max_chars - 1)].rstrip() + "…"
    return joined


def fold_search_text(text: str) -> str:
    """Casefold + strip accents so ``como`` matches ``cómo``."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _fold_with_index_map(text: str) -> tuple[str, list[int]]:
    """Folded string plus original index for each folded character."""
    folded: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        for piece in unicodedata.normalize("NFKD", char.casefold()):
            if unicodedata.combining(piece):
                continue
            folded.append(piece)
            mapping.append(index)
    return "".join(folded), mapping


def preview_match(
    text: str,
    query: str,
    *,
    max_chars: int = DEFAULT_PREVIEW_CHARS,
    max_lines: int = DEFAULT_PREVIEW_LINES,
) -> str:
    """Preview centered on the first match so a short hint reveals the needle."""
    needle = " ".join(fold_search_text(query).split())
    if not needle:
        return preview_text(text, max_chars=max_chars, max_lines=max_lines)

    flat = " ".join(text.split())
    if not flat:
        return preview_text(text, max_chars=max_chars, max_lines=max_lines)

    spans = find_match_spans(flat, query)
    if not spans:
        return preview_text(text, max_chars=max_chars, max_lines=max_lines)

    start_orig, end_orig = spans[0]
    window = max(8, max_chars)
    pad = max(0, (window - (end_orig - start_orig)) // 2)
    start = max(0, start_orig - pad)
    end = min(len(flat), start + window)
    if end - start < window:
        start = max(0, end - window)
    snippet = flat[start:end]
    if start > 0:
        snippet = "…" + snippet.lstrip()
    if end < len(flat):
        snippet = snippet.rstrip() + "…"
    return snippet


def find_match_spans(text: str, query: str) -> tuple[tuple[int, int], ...]:
    """Original-text ranges that match ``query`` (full needle, else each token)."""
    needle = " ".join(fold_search_text(query).split())
    if not needle or not text:
        return ()

    folded, mapping = _fold_with_index_map(text)
    if not folded or not mapping:
        return ()

    targets: tuple[str, ...]
    if needle in folded:
        targets = (needle,)
    else:
        targets = tuple(token for token in needle.split() if token)

    raw: list[tuple[int, int]] = []
    for target in targets:
        start = 0
        while True:
            at = folded.find(target, start)
            if at < 0:
                break
            end_fold = at + len(target)
            if at < len(mapping) and end_fold <= len(mapping):
                raw.append((mapping[at], mapping[end_fold - 1] + 1))
            start = at + max(1, len(target))

    if not raw:
        return ()

    raw.sort()
    merged: list[tuple[int, int]] = [raw[0]]
    for start, end in raw[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def format_copied_ago(copied_at: float, *, now: float) -> str:
    elapsed = max(0, int(now - copied_at))
    if elapsed < 10:
        return "Copiado ahora"
    if elapsed < 60:
        return "Copiado hace un momento"
    minutes = elapsed // 60
    if minutes == 1:
        return "Copiado hace 1 minuto"
    if minutes < 60:
        return f"Copiado hace {minutes} minutos"
    hours = minutes // 60
    if hours == 1:
        return "Copiado hace 1 hora"
    if hours < 24:
        return f"Copiado hace {hours} horas"
    days = hours // 24
    if days == 1:
        return "Copiado hace 1 día"
    return f"Copiado hace {days} días"


def search_entries(entries: tuple[ClipboardEntry, ...], query: str) -> tuple[ClipboardEntry, ...]:
    """Filter history by a partial hint (case/accent-insensitive substring or tokens)."""
    needle = " ".join(fold_search_text(query).split())
    if not needle:
        return entries
    tokens = needle.split()
    matches: list[ClipboardEntry] = []
    for entry in entries:
        if entry.is_image:
            haystack = fold_search_text(f"imagen image {entry.mime}")
        else:
            haystack = fold_search_text(entry.text)
        if needle in haystack or all(token in haystack for token in tokens):
            matches.append(entry)
    return tuple(matches)


def entry_storage_bytes(entry: ClipboardEntry, *, data_dir: Path) -> int:
    if entry.is_image:
        absolute = resolve_image_path(entry.path, data_dir=data_dir)
        if absolute is None:
            return 0
        try:
            return int(absolute.stat().st_size)
        except OSError:
            return 0
    return len(entry.text.encode("utf-8"))


def resolve_image_path(relative: str, *, data_dir: Path) -> Path | None:
    cleaned = (relative or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or ".." in cleaned.split("/"):
        return None
    if not cleaned.startswith(f"{IMAGE_REL_PREFIX}/"):
        return None
    absolute = (data_dir / cleaned).resolve()
    try:
        absolute.relative_to(data_dir.resolve())
    except ValueError:
        return None
    return absolute


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def image_relative_path(content_hash: str) -> str:
    return f"{IMAGE_REL_PREFIX}/{content_hash}.png"


class ClipboardHistory:
    """Newest-first history. Consecutive duplicates ignored; older copies promoted."""

    def __init__(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        max_item_bytes: int | None = None,
        max_text_bytes: int | None = None,
        max_history_bytes: int = DEFAULT_MAX_HISTORY_BYTES,
        data_dir: Path | None = None,
        images_dir: Path | None = None,
    ) -> None:
        text_limit = (
            max_text_bytes
            if max_text_bytes is not None
            else (max_item_bytes if max_item_bytes is not None else DEFAULT_MAX_TEXT_BYTES)
        )
        self._limit = max(1, int(limit))
        self._max_text_bytes = max(1, int(text_limit))
        self._max_history_bytes = max(self._max_text_bytes, int(max_history_bytes))
        self._data_dir = data_dir if data_dir is not None else Path(".")
        self._images_dir = (
            images_dir if images_dir is not None else self._data_dir / IMAGE_REL_PREFIX
        )
        self._items: list[ClipboardEntry] = []
        self._next_serial = 1

    @property
    def entries(self) -> tuple[ClipboardEntry, ...]:
        return tuple(self._items)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def entry_by_id(self, entry_id: str) -> ClipboardEntry | None:
        for entry in self._items:
            if entry.id == entry_id:
                return entry
        return None

    def remember(self, text: str, *, now: float) -> bool:
        """Backward-compatible text ingest. Return True when history changed."""
        return self.remember_text(text, now=now)

    def remember_text(self, text: str, *, now: float) -> bool:
        if not text:
            return False
        if len(text.encode("utf-8")) > self._max_text_bytes:
            # Discard from history only; system clipboard is untouched.
            return False
        if self._items and self._items[0].is_text and self._items[0].text == text:
            return False
        removed = [item for item in self._items if item.is_text and item.text == text]
        self._items = [item for item in self._items if not (item.is_text and item.text == text)]
        entry = ClipboardEntry(
            id=self._new_id(now),
            text=text,
            copied_at=now,
            kind=ENTRY_TEXT,
        )
        self._items.insert(0, entry)
        dropped = self._enforce_limits()
        self._delete_image_files(removed + dropped)
        return True

    def remember_image(
        self,
        payload: bytes,
        *,
        mime: str,
        now: float,
        content_hash: str | None = None,
    ) -> bool:
        """Store PNG bytes on disk and index them. ``payload`` must already be PNG."""
        if not payload:
            return False
        digest = content_hash or hash_bytes(payload)
        if (
            self._items
            and self._items[0].is_image
            and self._items[0].content_hash == digest
        ):
            return False

        relative = image_relative_path(digest)
        absolute = self._data_dir / relative
        existing = next(
            (item for item in self._items if item.is_image and item.content_hash == digest),
            None,
        )
        removed = [
            item
            for item in self._items
            if item.is_image and item.content_hash == digest
        ]
        self._items = [
            item
            for item in self._items
            if not (item.is_image and item.content_hash == digest)
        ]

        if existing is None:
            try:
                absolute.parent.mkdir(parents=True, exist_ok=True)
                tmp = absolute.with_suffix(absolute.suffix + ".tmp")
                tmp.write_bytes(payload)
                tmp.replace(absolute)
                os.chmod(absolute, 0o600)
            except OSError:
                return False
        else:
            relative = existing.path or relative

        entry = ClipboardEntry(
            id=self._new_id(now),
            text="",
            copied_at=now,
            kind=ENTRY_IMAGE,
            mime=(mime or "image/png").strip() or "image/png",
            path=relative,
            content_hash=digest,
        )
        self._items.insert(0, entry)
        dropped = self._enforce_limits()
        # Keep the file we just referenced; only delete truly unused paths.
        self._delete_image_files(removed + dropped)
        return True

    def remove_entry(self, entry_id: str) -> bool:
        target = self.entry_by_id(entry_id)
        if target is None:
            return False
        self._items = [item for item in self._items if item.id != entry_id]
        self._delete_image_files([target])
        return True

    def replace_entries(self, entries: tuple[ClipboardEntry, ...]) -> None:
        self._items = list(entries)
        dropped = self._enforce_limits()
        self._delete_image_files(dropped)
        serials: list[int] = []
        for entry in self._items:
            _, _, serial = entry.id.partition("-")
            try:
                serials.append(int(serial))
            except ValueError:
                continue
        if serials:
            self._next_serial = max(serials) + 1

    def total_bytes(self) -> int:
        return sum(entry_storage_bytes(item, data_dir=self._data_dir) for item in self._items)

    def cleanup_orphans(self) -> int:
        """Delete image files under images_dir that no entry references."""
        return cleanup_orphan_images(
            self._images_dir,
            self._items,
            data_dir=self._data_dir,
        )

    def _enforce_limits(self) -> list[ClipboardEntry]:
        dropped: list[ClipboardEntry] = []
        if len(self._items) > self._limit:
            dropped.extend(self._items[self._limit :])
            del self._items[self._limit :]
        while self._items and self.total_bytes() > self._max_history_bytes:
            dropped.append(self._items.pop())
        return dropped

    def _delete_image_files(self, entries: list[ClipboardEntry]) -> None:
        live_paths = {
            item.path
            for item in self._items
            if item.is_image and item.path
        }
        for entry in entries:
            if not entry.is_image or not entry.path or entry.path in live_paths:
                continue
            absolute = resolve_image_path(entry.path, data_dir=self._data_dir)
            if absolute is None or not absolute.is_file():
                continue
            try:
                absolute.unlink()
            except OSError:
                pass

    def _new_id(self, now: float) -> str:
        ident = f"{int(now * 1000)}-{self._next_serial}"
        self._next_serial += 1
        return ident


def cleanup_orphan_images(
    images_dir: Path,
    entries: tuple[ClipboardEntry, ...] | list[ClipboardEntry],
    *,
    data_dir: Path,
) -> int:
    if not images_dir.is_dir():
        return 0
    referenced: set[Path] = set()
    for entry in entries:
        if not entry.is_image:
            continue
        absolute = resolve_image_path(entry.path, data_dir=data_dir)
        if absolute is not None:
            referenced.add(absolute.resolve())
    removed = 0
    try:
        candidates = list(images_dir.iterdir())
    except OSError:
        return 0
    for path in candidates:
        if not path.is_file():
            continue
        if path.suffix.lower() == ".tmp":
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        if path.resolve() in referenced:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def load_history(path: Path) -> tuple[ClipboardEntry, ...]:
    """Compatibility wrapper — returns entries only."""
    return load_history_result(path).entries


def load_history_result(path: Path) -> HistoryLoadResult:
    if not path.is_file():
        return HistoryLoadResult(entries=(), trusted=True, version=HISTORY_VERSION)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt: keep working with empty history but do NOT wipe image files.
        return HistoryLoadResult(entries=(), trusted=False, version=0)
    if not isinstance(payload, dict):
        return HistoryLoadResult(entries=(), trusted=False, version=0)
    try:
        version = int(payload.get("version", 1) or 1)
    except (TypeError, ValueError):
        version = 1
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return HistoryLoadResult(entries=(), trusted=False, version=version)
    items: list[ClipboardEntry] = []
    seen_ids: set[str] = set()
    for entry in raw_items:
        parsed = _entry_from_dict(entry)
        if parsed is None or parsed.id in seen_ids:
            continue
        seen_ids.add(parsed.id)
        items.append(parsed)
    return HistoryLoadResult(entries=tuple(items), trusted=True, version=version)


def save_history(path: Path, entries: tuple[ClipboardEntry, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": HISTORY_VERSION,
        "items": [_entry_to_dict(entry) for entry in entries],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
        path.chmod(0o600)
    except OSError:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _entry_from_dict(entry: object) -> ClipboardEntry | None:
    if not isinstance(entry, dict):
        return None
    ident = str(entry.get("id", "")).strip()
    if not ident:
        return None
    try:
        copied_at = float(entry.get("copied_at", 0) or 0)
    except (TypeError, ValueError):
        copied_at = 0.0

    kind = str(entry.get("type") or entry.get("kind") or "").strip().casefold()
    if not kind:
        # v1 entries are text-only.
        kind = ENTRY_IMAGE if entry.get("path") else ENTRY_TEXT

    if kind == ENTRY_IMAGE:
        relative = str(entry.get("path", "")).strip().replace("\\", "/")
        if not relative or ".." in relative.split("/"):
            return None
        mime = str(entry.get("mime", "image/png") or "image/png").strip() or "image/png"
        digest = str(entry.get("content_hash", "") or "").strip()
        if not digest:
            name = Path(relative).stem
            digest = name if len(name) >= 16 else ""
        return ClipboardEntry(
            id=ident,
            text="",
            copied_at=copied_at,
            kind=ENTRY_IMAGE,
            mime=mime,
            path=relative,
            content_hash=digest,
        )

    text = entry.get("text")
    if not isinstance(text, str) or text == "":
        return None
    return ClipboardEntry(
        id=ident,
        text=text,
        copied_at=copied_at,
        kind=ENTRY_TEXT,
    )


def _entry_to_dict(entry: ClipboardEntry) -> dict[str, object]:
    if entry.is_image:
        payload: dict[str, object] = {
            "id": entry.id,
            "type": ENTRY_IMAGE,
            "mime": entry.mime or "image/png",
            "path": entry.path,
            "copied_at": entry.copied_at,
        }
        if entry.content_hash:
            payload["content_hash"] = entry.content_hash
        return payload
    return {"id": entry.id, "text": entry.text, "copied_at": entry.copied_at}
