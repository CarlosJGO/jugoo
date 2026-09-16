"""Cooldown, snooze and mention memory for task reminders.

Persisted to disk so restarts of the watcher do not immediately re-fire
the same reminder. Separate from tasks.json (user data) and from the
notification history JSON (presentation layer).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ....runtime_paths import reminder_state_path


@dataclass
class TaskReminderMemory:
    last_notified_at: float = 0.0
    snooze_until: float = 0.0
    reminder_count: int = 0
    period_key: str = ""
    times_mentioned_today: int = 0
    day: str = ""
    last_mentioned_at: float = 0.0
    last_message: str = ""
    cooldown_until: float = 0.0


class ReminderState:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._items: dict[str, TaskReminderMemory] = {}
        if self._path is not None:
            self._load()

    def memory(self, task_id: str) -> TaskReminderMemory:
        item = self._items.setdefault(task_id, TaskReminderMemory())
        self._align_calendar_day(item)
        return item

    def align_period(self, task_id: str, period_key: str) -> TaskReminderMemory:
        item = self.memory(task_id)
        if item.period_key and item.period_key != period_key:
            item.last_notified_at = 0.0
            item.reminder_count = 0
            item.cooldown_until = 0.0
        item.period_key = period_key
        return item

    def mark_notified(
        self,
        task_id: str,
        period_key: str,
        now: float,
        *,
        message: str = "",
        cooldown_sec: float | None = None,
    ) -> None:
        item = self.align_period(task_id, period_key)
        item.last_notified_at = now
        item.reminder_count += 1
        if cooldown_sec is not None and cooldown_sec > 0:
            item.cooldown_until = now + float(cooldown_sec)
        cleaned = " ".join(str(message).split()).strip()
        if cleaned:
            item.last_message = cleaned
            item.last_mentioned_at = now
            item.times_mentioned_today += 1
            item.day = _today_key(now)
        self._save()

    def snooze(self, task_id: str, until: float) -> None:
        self.memory(task_id).snooze_until = until
        self._save()

    def forget(self, task_id: str) -> None:
        if task_id in self._items:
            self._items.pop(task_id, None)
            self._save()

    def _align_calendar_day(self, item: TaskReminderMemory, now: float | None = None) -> None:
        today = _today_key(now if now is not None else datetime.now().timestamp())
        if item.day and item.day != today:
            item.times_mentioned_today = 0
            item.day = today
        elif not item.day:
            item.day = today

    def _load(self) -> None:
        path = self._path
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        loaded: dict[str, TaskReminderMemory] = {}
        for task_id, raw in payload.items():
            key = str(task_id).strip()
            if not key or not isinstance(raw, dict):
                continue
            item = TaskReminderMemory(
                last_notified_at=_as_timestamp(raw.get("last_mentioned_at", raw.get("last_notified_at"))),
                snooze_until=_as_timestamp(raw.get("snooze_until")),
                reminder_count=_as_nonneg_int(raw.get("reminder_count")),
                period_key=str(raw.get("period_key", "") or ""),
                times_mentioned_today=_as_nonneg_int(raw.get("times_mentioned_today")),
                day=str(raw.get("day", "") or ""),
                last_mentioned_at=_as_timestamp(raw.get("last_mentioned_at")),
                last_message=str(raw.get("last_message", "") or "").strip(),
                cooldown_until=_as_timestamp(raw.get("cooldown_until")),
            )
            if item.last_notified_at <= 0 and item.last_mentioned_at > 0:
                item.last_notified_at = item.last_mentioned_at
            self._align_calendar_day(item)
            loaded[key] = item
        self._items = loaded

    def _save(self) -> None:
        path = self._path
        if path is None:
            return
        payload: dict[str, dict[str, Any]] = {}
        for task_id, item in self._items.items():
            self._align_calendar_day(item)
            payload[task_id] = {
                "times_mentioned_today": int(item.times_mentioned_today),
                "day": item.day,
                "last_mentioned_at": _ts_to_iso(item.last_mentioned_at or item.last_notified_at),
                "last_message": item.last_message,
                "cooldown_until": _ts_to_iso(item.cooldown_until),
                "snooze_until": _ts_to_iso(item.snooze_until),
                "reminder_count": int(item.reminder_count),
                "period_key": item.period_key,
                "last_notified_at": _ts_to_iso(item.last_notified_at),
            }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            # Persistence must never break the reminder notification path.
            return


def _today_key(now: float | None = None) -> str:
    if now is None:
        return date.today().isoformat()
    return datetime.fromtimestamp(now).date().isoformat()


def _ts_to_iso(value: float) -> str:
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value).replace(microsecond=0).isoformat()


def _as_timestamp(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _as_nonneg_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def default_reminder_state() -> ReminderState:
    return ReminderState(reminder_state_path())
