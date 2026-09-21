"""Local cache / link store for AIDYC tasks shown in Jugoo."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


CACHE_VERSION = 1


@dataclass
class AidycTaskLink:
    """Local representation of an AIDYC task (cache, not source of truth)."""

    source: str
    aidyc_task_id: int
    padre_db: str
    last_known_version: int
    last_sync: str
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def local_id(self) -> str:
        return f"aidyc:{self.aidyc_task_id}"


@dataclass
class AidycCacheState:
    padre_db: str = ""
    last_sync: str = ""
    connected: bool = False
    last_error: str = ""
    items: tuple[AidycTaskLink, ...] = ()


def load_cache(path: Path) -> AidycCacheState:
    if not path.is_file():
        return AidycCacheState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AidycCacheState()
    if not isinstance(payload, dict):
        return AidycCacheState()
    raw_items = payload.get("items", [])
    items: list[AidycTaskLink] = []
    if isinstance(raw_items, list):
        for entry in raw_items:
            link = _link_from_dict(entry)
            if link is not None:
                items.append(link)
    return AidycCacheState(
        padre_db=str(payload.get("padre_db", "") or ""),
        last_sync=str(payload.get("last_sync", "") or ""),
        connected=bool(payload.get("connected", False)),
        last_error=str(payload.get("last_error", "") or ""),
        items=tuple(items),
    )


def save_cache(path: Path, state: AidycCacheState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "padre_db": state.padre_db,
        "last_sync": state.last_sync,
        "connected": state.connected,
        "last_error": state.last_error,
        "items": [asdict(item) for item in state.items],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def upsert_from_remote(
    state: AidycCacheState,
    tareas: list[dict[str, Any]],
    *,
    padre_db: str,
    replace_all: bool = True,
) -> AidycCacheState:
    now = datetime.now().isoformat(timespec="seconds")
    by_id: dict[int, AidycTaskLink] = {}
    if not replace_all:
        for item in state.items:
            by_id[item.aidyc_task_id] = item

    for raw in tareas:
        if not isinstance(raw, dict):
            continue
        try:
            task_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        try:
            version = int(raw.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        by_id[task_id] = AidycTaskLink(
            source="aidyc",
            aidyc_task_id=task_id,
            padre_db=padre_db,
            last_known_version=version,
            last_sync=now,
            snapshot=dict(raw),
        )

    items = tuple(sorted(by_id.values(), key=lambda item: item.aidyc_task_id))
    return AidycCacheState(
        padre_db=padre_db,
        last_sync=now,
        connected=True,
        last_error="",
        items=items,
    )


def mark_disconnected(state: AidycCacheState, error: str) -> AidycCacheState:
    return AidycCacheState(
        padre_db=state.padre_db,
        last_sync=state.last_sync,
        connected=False,
        last_error=str(error or "")[:300],
        items=state.items,
    )


def _link_from_dict(entry: object) -> AidycTaskLink | None:
    if not isinstance(entry, dict):
        return None
    try:
        task_id = int(entry.get("aidyc_task_id"))
    except (TypeError, ValueError):
        return None
    snapshot = entry.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    try:
        version = int(entry.get("last_known_version", 1) or 1)
    except (TypeError, ValueError):
        version = 1
    return AidycTaskLink(
        source=str(entry.get("source", "aidyc") or "aidyc"),
        aidyc_task_id=task_id,
        padre_db=str(entry.get("padre_db", "") or ""),
        last_known_version=version,
        last_sync=str(entry.get("last_sync", "") or ""),
        snapshot=snapshot,
    )
