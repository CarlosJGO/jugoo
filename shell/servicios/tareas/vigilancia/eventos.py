"""Presence events for the task watcher. Names match EventBus subscribers."""

from __future__ import annotations

TASK_WATCHER_HEARTBEAT = "task_watcher_heartbeat"
TASK_WATCHER_REMINDER = "task_watcher_reminder"
TASK_WATCHER_AI_REMINDER = "task_watcher_ai_reminder"

KIND_HEARTBEAT = "heartbeat"
KIND_REMINDER = "reminder"
KIND_AI_REMINDER = "ai_reminder"

TASK_WATCHER_OBJECT_PATH = "/com/jugoo/TaskWatcher"
TASK_WATCHER_INTERFACE = "com.jugoo.TaskWatcher"
TASK_WATCHER_SIGNAL = "Pulse"

_KIND_TO_EVENT = {
    KIND_HEARTBEAT: TASK_WATCHER_HEARTBEAT,
    KIND_REMINDER: TASK_WATCHER_REMINDER,
    KIND_AI_REMINDER: TASK_WATCHER_AI_REMINDER,
}


def event_name_for_kind(kind: str) -> str:
    return _KIND_TO_EVENT.get(str(kind), TASK_WATCHER_HEARTBEAT)
