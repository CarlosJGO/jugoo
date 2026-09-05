"""Atomic JSON persistence for shell tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...models import TASK_REPEAT_NONE, TASK_REPEATS, TaskRecord

TASKS_STORE_VERSION = 1


def load_tasks(path: Path) -> tuple[TaskRecord, ...]:
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"shell: tasks: could not load {path}: {error}")
        return ()
    if not isinstance(payload, dict):
        return ()
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return ()
    items: list[TaskRecord] = []
    seen: set[str] = set()
    for entry in raw_items:
        record = _record_from_dict(entry)
        if record is None or record.id in seen:
            continue
        seen.add(record.id)
        items.append(record)
    return tuple(items)


def save_tasks(path: Path, tasks: tuple[TaskRecord, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": TASKS_STORE_VERSION,
        "items": [_record_to_dict(item) for item in tasks],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as error:
        print(f"shell: tasks: could not save {path}: {error}")
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _record_from_dict(entry: object) -> TaskRecord | None:
    if not isinstance(entry, dict):
        return None
    ident = str(entry.get("id", "")).strip()
    title = str(entry.get("title", "")).strip()
    if not ident or not title:
        return None
    repeat = str(entry.get("repeat", TASK_REPEAT_NONE)).strip()
    if repeat not in TASK_REPEATS:
        repeat = TASK_REPEAT_NONE
    due_raw = entry.get("due_date")
    due_date = str(due_raw).strip()[:10] if due_raw else None
    if due_date == "":
        due_date = None
    try:
        month_day = int(entry.get("month_day", 1) or 1)
    except (TypeError, ValueError):
        month_day = 1
    return TaskRecord(
        id=ident,
        title=title,
        notes=str(entry.get("notes", "")).strip(),
        repeat=repeat,
        due_date=due_date,
        month_day=max(1, min(month_day, 31)),
        created_at=str(entry.get("created_at", "")).strip(),
        period_cursor=str(entry.get("period_cursor", "")).strip(),
        completed_periods=_string_tuple(entry.get("completed_periods")),
        missed_periods=_string_tuple(entry.get("missed_periods")),
    )


def _record_to_dict(task: TaskRecord) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "notes": task.notes,
        "repeat": task.repeat,
        "due_date": task.due_date,
        "month_day": task.month_day,
        "created_at": task.created_at,
        "period_cursor": task.period_cursor,
        "completed_periods": list(task.completed_periods),
        "missed_periods": list(task.missed_periods),
    }


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    seen: set[str] = set()
    for entry in value:
        text = str(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return tuple(items)
