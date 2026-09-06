"""In-memory clipboard history with local JSON persistence. Never logs payload text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HISTORY_VERSION = 1
DEFAULT_LIMIT = 200
DEFAULT_MAX_ITEM_BYTES = 512 * 1024
DEFAULT_PREVIEW_CHARS = 96
DEFAULT_PREVIEW_LINES = 2


@dataclass(frozen=True)
class ClipboardEntry:
    id: str
    text: str
    copied_at: float


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
    needle = " ".join(query.casefold().split())
    if not needle:
        return entries
    tokens = needle.split()
    matches: list[ClipboardEntry] = []
    for entry in entries:
        haystack = entry.text.casefold()
        if needle in haystack or all(token in haystack for token in tokens):
            matches.append(entry)
    return tuple(matches)


class ClipboardHistory:
    """Newest-first history. Consecutive duplicates are ignored; older copies are promoted."""

    def __init__(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        max_item_bytes: int = DEFAULT_MAX_ITEM_BYTES,
    ) -> None:
        self._limit = max(1, int(limit))
        self._max_item_bytes = max(1, int(max_item_bytes))
        self._items: list[ClipboardEntry] = []
        self._next_serial = 1

    @property
    def entries(self) -> tuple[ClipboardEntry, ...]:
        return tuple(self._items)

    def entry_by_id(self, entry_id: str) -> ClipboardEntry | None:
        for entry in self._items:
            if entry.id == entry_id:
                return entry
        return None

    def remember(self, text: str, *, now: float) -> bool:
        """Return True when the visible history changed."""
        if not text:
            return False
        if len(text.encode("utf-8")) > self._max_item_bytes:
            return False
        if self._items and self._items[0].text == text:
            return False
        self._items = [item for item in self._items if item.text != text]
        entry = ClipboardEntry(id=self._new_id(now), text=text, copied_at=now)
        self._items.insert(0, entry)
        del self._items[self._limit :]
        return True

    def replace_entries(self, entries: tuple[ClipboardEntry, ...]) -> None:
        trimmed = list(entries[: self._limit])
        self._items = trimmed
        serials: list[int] = []
        for entry in trimmed:
            _, _, serial = entry.id.partition("-")
            try:
                serials.append(int(serial))
            except ValueError:
                continue
        if serials:
            self._next_serial = max(serials) + 1

    def _new_id(self, now: float) -> str:
        ident = f"{int(now * 1000)}-{self._next_serial}"
        self._next_serial += 1
        return ident


def load_history(path: Path) -> tuple[ClipboardEntry, ...]:
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return ()
    items: list[ClipboardEntry] = []
    seen_ids: set[str] = set()
    for entry in raw_items:
        parsed = _entry_from_dict(entry)
        if parsed is None or parsed.id in seen_ids:
            continue
        seen_ids.add(parsed.id)
        items.append(parsed)
    return tuple(items)


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
    text = entry.get("text")
    if not ident or not isinstance(text, str) or text == "":
        return None
    try:
        copied_at = float(entry.get("copied_at", 0) or 0)
    except (TypeError, ValueError):
        copied_at = 0.0
    return ClipboardEntry(id=ident, text=text, copied_at=copied_at)


def _entry_to_dict(entry: ClipboardEntry) -> dict[str, object]:
    return {"id": entry.id, "text": entry.text, "copied_at": entry.copied_at}
