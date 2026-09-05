"""Atomic JSON persistence for pinned dock apps and launcher favorites."""

from __future__ import annotations

import json
from pathlib import Path

from ...models import normalize_desktop_id

APPLICATION_PREFS_VERSION = 2


def load_pinned_ids(path: Path) -> tuple[str, ...]:
    pinned, _favorites = load_application_prefs(path)
    return pinned


def load_application_prefs(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(pinned, favorites)``. Version 1 files only have pinned."""
    if not path.is_file():
        return (), ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"shell: applications: could not load app prefs {path}: {error}")
        return (), ()

    if isinstance(payload, dict):
        pinned = _unique_ids(payload.get("pinned", []))
        favorites = _unique_ids(payload.get("favorites", []))
        return pinned, favorites
    if isinstance(payload, list):
        return _unique_ids(payload), ()
    return (), ()


def save_pinned_ids(path: Path, pinned_ids: tuple[str, ...]) -> None:
    _favorites = load_application_prefs(path)[1]
    save_application_prefs(path, pinned_ids, _favorites)


def save_application_prefs(
    path: Path,
    pinned_ids: tuple[str, ...],
    favorite_ids: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": APPLICATION_PREFS_VERSION,
        "pinned": [normalize_desktop_id(item) for item in pinned_ids if normalize_desktop_id(item)],
        "favorites": [
            normalize_desktop_id(item) for item in favorite_ids if normalize_desktop_id(item)
        ],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as error:
        print(f"shell: applications: could not save app prefs {path}: {error}")
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _unique_ids(raw_items: object) -> tuple[str, ...]:
    if not isinstance(raw_items, list):
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in raw_items:
        ident = normalize_desktop_id(str(entry))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        ordered.append(ident)
    return tuple(ordered)
