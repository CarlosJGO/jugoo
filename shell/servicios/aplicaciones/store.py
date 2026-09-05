"""Atomic JSON persistence for the pinned-application order."""

from __future__ import annotations

import json
from pathlib import Path

from ...models import normalize_desktop_id

PINNED_APPS_VERSION = 1


def load_pinned_ids(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"shell: applications: could not load pinned apps {path}: {error}")
        return ()

    if isinstance(payload, dict):
        raw_items = payload.get("pinned", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        return ()

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


def save_pinned_ids(path: Path, pinned_ids: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PINNED_APPS_VERSION,
        "pinned": [normalize_desktop_id(item) for item in pinned_ids if normalize_desktop_id(item)],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as error:
        print(f"shell: applications: could not save pinned apps {path}: {error}")
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass
