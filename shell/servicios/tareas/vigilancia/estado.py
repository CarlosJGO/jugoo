"""In-memory cooldown and snooze. Lost on restart by design."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskReminderMemory:
    last_notified_at: float = 0.0
    snooze_until: float = 0.0
    reminder_count: int = 0
    period_key: str = ""


class ReminderState:
    def __init__(self) -> None:
        self._items: dict[str, TaskReminderMemory] = {}

    def memory(self, task_id: str) -> TaskReminderMemory:
        return self._items.setdefault(task_id, TaskReminderMemory())

    def align_period(self, task_id: str, period_key: str) -> TaskReminderMemory:
        item = self.memory(task_id)
        if item.period_key and item.period_key != period_key:
            item.last_notified_at = 0.0
            item.reminder_count = 0
        item.period_key = period_key
        return item

    def mark_notified(self, task_id: str, period_key: str, now: float) -> None:
        item = self.align_period(task_id, period_key)
        item.last_notified_at = now
        item.reminder_count += 1

    def snooze(self, task_id: str, until: float) -> None:
        self.memory(task_id).snooze_until = until

    def forget(self, task_id: str) -> None:
        self._items.pop(task_id, None)
