"""Persistent recently used emoji list."""

from __future__ import annotations

import json
from pathlib import Path

from .catalogo import EmojiRecord


class EmojiRecentStore:
    def __init__(self, path: Path, *, limit: int = 24) -> None:
        self._path = path
        self._limit = max(1, limit)

    def recent(self, catalog: tuple[EmojiRecord, ...]) -> tuple[EmojiRecord, ...]:
        by_glyph = {emoji.glyph: emoji for emoji in catalog}
        return tuple(
            by_glyph[glyph]
            for glyph in self._read()
            if glyph in by_glyph
        )

    def remember(self, glyph: str) -> None:
        if not glyph:
            return
        glyphs = [glyph, *(item for item in self._read() if item != glyph)][: self._limit]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(glyphs, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return

    def _read(self) -> tuple[str, ...]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        if not isinstance(payload, list):
            return ()
        return tuple(dict.fromkeys(item for item in payload if isinstance(item, str) and item))