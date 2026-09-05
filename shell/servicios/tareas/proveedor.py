"""Task sources. Local JSON now; remote providers can share this contract later."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

from ...models import TaskRecord, TaskSnapshot
from ...runtime_paths import tasks_path
from .logic import complete_task, rollover_tasks, today_board
from .store import load_tasks, save_tasks


class TaskProvider(Protocol):
    """Read-only access plus completing the current occurrence."""

    def records(self) -> tuple[TaskRecord, ...]:
        ...

    def board(self, today: date | None = None) -> tuple[TaskSnapshot, ...]:
        ...

    def reload_if_changed(self) -> bool:
        ...

    def complete(self, task_id: str, *, today: date | None = None) -> bool:
        ...


class LocalTaskProvider:
    """Caches ``tasks.json`` and reloads only when the file timestamp changes."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else tasks_path()
        self._tasks: tuple[TaskRecord, ...] = ()
        self._mtime_ns: int = 0
        self.reload_if_changed(force=True)

    @property
    def path(self) -> Path:
        return self._path

    def records(self) -> tuple[TaskRecord, ...]:
        return self._tasks

    def board(self, today: date | None = None) -> tuple[TaskSnapshot, ...]:
        when = today if today is not None else date.today()
        return today_board(rollover_tasks(self._tasks, when), when)

    def reload_if_changed(self, *, force: bool = False) -> bool:
        mtime_ns = _path_mtime_ns(self._path)
        if not force and mtime_ns == self._mtime_ns:
            return False
        if not force and mtime_ns != 0 and mtime_ns <= self._mtime_ns:
            return False
        self._tasks = load_tasks(self._path)
        self._mtime_ns = mtime_ns
        return True

    def complete(self, task_id: str, *, today: date | None = None) -> bool:
        self.reload_if_changed()
        when = today if today is not None else date.today()
        updated: list[TaskRecord] = []
        changed = False
        for task in self._tasks:
            if task.id == task_id:
                completed = complete_task(task, when)
                if completed != task:
                    changed = True
                updated.append(completed)
            else:
                updated.append(task)
        if not changed:
            return False
        self._tasks = tuple(updated)
        save_tasks(self._path, self._tasks)
        self._mtime_ns = _path_mtime_ns(self._path)
        return True


def _path_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0
