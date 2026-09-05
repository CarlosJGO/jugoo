"""Persisted tasks with daily/monthly rollover, exposed through EventBus."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

from ...config import TASKS_ROLLOVER_INTERVAL_SEC
from ...eventbus import EventBus
from ...models import (
    TASK_REPEAT_NONE,
    TASK_REPEATS,
    TaskRecord,
    TaskSnapshot,
    TasksSnapshot,
)
from ...runtime_paths import tasks_path
from .logic import (
    overdue_count,
    parse_iso_date,
    pending_today_count,
    period_key,
    marked_days as marked_days_for,
    rollover_tasks,
    tasks_for_date as snapshots_for_date,
    today_board,
    toggle_task,
    upcoming_tasks as upcoming_for,
)
from .store import load_tasks, save_tasks

TASKS_CHANGED = "tasks_changed"
TASKS_PANEL_REQUESTED = "tasks_panel_requested"

_ROLLOVER_INTERVAL_MS = TASKS_ROLLOVER_INTERVAL_SEC * 1000


class TasksService:
    """Owns task records, midnight rollover, and snapshots for widgets."""

    def __init__(self, event_bus: EventBus, *, path: Path | None = None) -> None:
        self._event_bus = event_bus
        self._path = path if path is not None else tasks_path()
        self._tasks: tuple[TaskRecord, ...] = ()
        self._today = date.today()
        self._mtime_ns: int = 0
        self._rollover_source_id: int = 0

    def start(self) -> None:
        self._tasks = rollover_tasks(load_tasks(self._path), date.today())
        self._today = date.today()
        self._persist()
        self._emit()
        if self._rollover_source_id:
            GLib.source_remove(self._rollover_source_id)
        self._rollover_source_id = GLib.timeout_add(
            _ROLLOVER_INTERVAL_MS,
            self._on_rollover_tick,
        )

    def close(self) -> None:
        if self._rollover_source_id:
            GLib.source_remove(self._rollover_source_id)
            self._rollover_source_id = 0

    @property
    def snapshot(self) -> TasksSnapshot:
        today = date.today()
        board = today_board(self._tasks, today)
        return TasksSnapshot(
            today=today.isoformat(),
            tasks=board,
            overdue_count=overdue_count(self._tasks, today),
            pending_today_count=pending_today_count(self._tasks, today),
        )

    def records(self) -> tuple[TaskRecord, ...]:
        return self._tasks

    def tasks_for_date(self, on_date: date) -> tuple[TaskSnapshot, ...]:
        return snapshots_for_date(self._tasks, on_date, date.today())

    def upcoming(self) -> tuple[TaskSnapshot, ...]:
        return upcoming_for(self._tasks, date.today())

    def marked_days(self, year: int, month: int) -> frozenset[int]:
        return marked_days_for(self._tasks, year, month)

    def add_task(
        self,
        title: str,
        *,
        notes: str = "",
        repeat: str = TASK_REPEAT_NONE,
        due_date: str | None = None,
        month_day: int = 1,
    ) -> TaskRecord | None:
        cleaned = title.strip()
        if not cleaned:
            return None
        kind = repeat if repeat in TASK_REPEATS else TASK_REPEAT_NONE
        today = date.today()
        due = (due_date or "").strip()[:10] or None
        if kind == TASK_REPEAT_NONE:
            parsed = parse_iso_date(due) or today
            due = parsed.isoformat()
        created = datetime.now().isoformat(timespec="seconds")
        record = TaskRecord(
            id=uuid4().hex[:12],
            title=cleaned,
            notes=notes.strip(),
            repeat=kind,
            due_date=due if kind == TASK_REPEAT_NONE else None,
            month_day=max(1, min(int(month_day), 31)),
            created_at=created,
            period_cursor=period_key(kind, today, due_date=due),
        )
        self._tasks = self._tasks + (record,)
        self._persist_and_emit()
        return record

    def toggle(self, task_id: str, *, on_date: date | None = None) -> None:
        today = date.today()
        when = on_date if on_date is not None else today
        if when != today:
            return
        updated: list[TaskRecord] = []
        changed = False
        for task in self._tasks:
            if task.id == task_id:
                updated.append(toggle_task(task, today))
                changed = True
            else:
                updated.append(task)
        if not changed:
            return
        self._tasks = tuple(updated)
        self._persist_and_emit()

    def delete(self, task_id: str) -> None:
        remaining = tuple(task for task in self._tasks if task.id != task_id)
        if remaining == self._tasks:
            return
        self._tasks = remaining
        self._persist_and_emit()

    def request_panel(self) -> None:
        self._event_bus.emit(TASKS_PANEL_REQUESTED, None)

    def _on_rollover_tick(self) -> bool:
        reloaded = self._reload_if_external_change()
        today = date.today()
        rolled = rollover_tasks(self._tasks, today)
        if rolled != self._tasks or today != self._today:
            self._tasks = rolled
            self._today = today
            self._persist_and_emit()
        elif reloaded:
            self._emit()
        return True

    def _reload_if_external_change(self) -> bool:
        mtime_ns = _path_mtime_ns(self._path)
        if mtime_ns == 0 or mtime_ns <= self._mtime_ns:
            return False
        self._tasks = load_tasks(self._path)
        self._mtime_ns = mtime_ns
        return True

    def _persist_and_emit(self) -> None:
        self._persist()
        self._emit()

    def _persist(self) -> None:
        save_tasks(self._path, self._tasks)
        self._mtime_ns = _path_mtime_ns(self._path)

    def _emit(self) -> None:
        self._event_bus.emit(TASKS_CHANGED, self.snapshot)


def _path_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0
