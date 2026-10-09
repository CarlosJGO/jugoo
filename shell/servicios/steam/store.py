"""Atomic JSON persistence for Steam games the user ignored."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ...models import normalize_steam_appid

STEAM_PREFS_VERSION = 1


def load_ignored_appids(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"shell: steam: could not load prefs {path}: {error}")
        return ()
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("ignored", [])
    return unique_appids(raw) if isinstance(raw, list) else ()


def save_ignored_appids(path: Path, ignored: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": STEAM_PREFS_VERSION, "ignored": list(unique_appids(ignored))}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except OSError as error:
        print(f"shell: steam: could not save prefs {path}: {error}")
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def unique_appids(raw_items: Iterable[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in map(normalize_steam_appid, raw_items) if item))
