"""Atomic JSON persistence for notification history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...models import NotificationAction, NotificationSnapshot

HISTORY_VERSION = 1


def shell_notifications_history_path(base_dir: Path, relative_name: str) -> Path:
    return base_dir / relative_name


def load_history(path: Path) -> tuple[tuple[NotificationSnapshot, ...], bool, int, frozenset[str]]:
    """Return snapshots, paused flag, next notification id, and sound-muted app keys."""
    if not path.is_file():
        return (), False, 1, frozenset()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"shell: notifications: could not load history {path}: {error}")
        return (), False, 1, frozenset()

    if not isinstance(payload, dict):
        return (), False, 1, frozenset()

    paused = bool(payload.get("paused", False))
    next_id = int(payload.get("next_id", 1) or 1)
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return (), paused, next_id, _load_sound_muted_apps(payload)

    items: list[NotificationSnapshot] = []
    for entry in raw_items:
        snapshot = _snapshot_from_dict(entry)
        if snapshot is not None:
            items.append(snapshot)

    items.sort(key=lambda item: item.id)
    if items:
        next_id = max(next_id, max(item.id for item in items) + 1)
    return tuple(items), paused, next_id, _load_sound_muted_apps(payload)


def _load_sound_muted_apps(payload: dict[str, Any]) -> frozenset[str]:
    raw = payload.get("sound_muted_apps", ())
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(entry).casefold() for entry in raw if str(entry).strip())


def save_history(
    path: Path,
    *,
    items: tuple[NotificationSnapshot, ...],
    paused: bool,
    next_id: int,
    sound_muted_apps: frozenset[str] | set[str] = frozenset(),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": HISTORY_VERSION,
        "paused": paused,
        "next_id": next_id,
        "sound_muted_apps": sorted(str(app_key) for app_key in sound_muted_apps),
        "items": [_snapshot_to_dict(item) for item in items],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as error:
        print(f"shell: notifications: could not save history {path}: {error}")
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def trim_history(
    items: tuple[NotificationSnapshot, ...],
    max_count: int,
) -> tuple[NotificationSnapshot, ...]:
    if max_count <= 0 or len(items) <= max_count:
        return items

    ordered = sorted(items, key=lambda item: item.timestamp, reverse=True)
    active = [item for item in ordered if not item.dismissed]
    dismissed = [item for item in ordered if item.dismissed]

    kept: list[NotificationSnapshot] = []
    for bucket in (active, dismissed):
        for item in bucket:
            if len(kept) >= max_count:
                break
            kept.append(item)
        if len(kept) >= max_count:
            break

    kept.sort(key=lambda item: item.id)
    return tuple(kept)


def _snapshot_to_dict(snapshot: NotificationSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "app_name": snapshot.app_name,
        "app_icon": snapshot.app_icon,
        "icon_name": snapshot.icon_name,
        "image_path": snapshot.image_path,
        "summary": snapshot.summary,
        "body": snapshot.body,
        "actions": [
            {"key": action.key, "label": action.label}
            for action in snapshot.actions
        ],
        "urgency": snapshot.urgency,
        "timestamp": snapshot.timestamp,
        "expire_timeout_ms": snapshot.expire_timeout_ms,
        "read": snapshot.read,
        "dismissed": snapshot.dismissed,
        "expired": snapshot.expired,
    }


def _snapshot_from_dict(raw: Any) -> NotificationSnapshot | None:
    if not isinstance(raw, dict):
        return None
    try:
        actions_raw = raw.get("actions", [])
        actions: tuple[NotificationAction, ...] = ()
        if isinstance(actions_raw, list):
            parsed: list[NotificationAction] = []
            for entry in actions_raw:
                if isinstance(entry, dict) and entry.get("key"):
                    parsed.append(
                        NotificationAction(
                            key=str(entry["key"]),
                            label=str(entry.get("label", entry["key"])),
                        )
                    )
            actions = tuple(parsed)

        return NotificationSnapshot(
            id=int(raw["id"]),
            app_name=str(raw.get("app_name", "") or "Application"),
            app_icon=str(raw.get("app_icon", "") or ""),
            icon_name=str(raw.get("icon_name", "") or ""),
            image_path=str(raw.get("image_path", "") or ""),
            summary=str(raw.get("summary", "") or ""),
            body=str(raw.get("body", "") or ""),
            actions=actions,
            urgency=max(0, min(2, int(raw.get("urgency", 1)))),
            timestamp=float(raw.get("timestamp", 0.0)),
            expire_timeout_ms=int(raw.get("expire_timeout_ms", -1)),
            read=bool(raw.get("read", False)),
            dismissed=bool(raw.get("dismissed", False)),
            expired=bool(raw.get("expired", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None
