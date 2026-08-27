"""FIFO toast presentation queue (max visible slots, no GTK)."""

from __future__ import annotations

from collections import deque


class ToastPresentationQueue:
    """Tracks pending and visible toast notification ids."""

    def __init__(self, max_visible: int) -> None:
        self._max_visible = max(1, max_visible)
        self._pending: deque[int] = deque()
        self._visible: dict[int, int] = {}

    @property
    def max_visible(self) -> int:
        return self._max_visible

    @property
    def pending_ids(self) -> tuple[int, ...]:
        return tuple(self._pending)

    @property
    def visible_ids(self) -> tuple[int, ...]:
        ordered = sorted(self._visible.items(), key=lambda item: item[0])
        return tuple(notification_id for _, notification_id in ordered)

    def pending_count(self) -> int:
        return len(self._pending)

    def visible_count(self) -> int:
        return len(self._visible)

    def is_visible(self, notification_id: int) -> bool:
        return notification_id in self._visible.values()

    def is_tracked(self, notification_id: int) -> bool:
        return notification_id in self._pending or notification_id in self._visible.values()

    def enqueue(self, notification_id: int) -> None:
        if self.is_tracked(notification_id):
            return
        self._pending.append(notification_id)

    def promote(self) -> tuple[tuple[int, int], ...]:
        """Fill empty slots from the pending FIFO queue."""
        promoted: list[tuple[int, int]] = []
        for slot in range(self._max_visible):
            if slot in self._visible:
                continue
            if not self._pending:
                break
            notification_id = self._pending.popleft()
            self._visible[slot] = notification_id
            promoted.append((slot, notification_id))
        return tuple(promoted)

    def release(self, notification_id: int) -> int | None:
        for slot, visible_id in list(self._visible.items()):
            if visible_id == notification_id:
                del self._visible[slot]
                return slot
        return None

    def clear(self) -> tuple[int, ...]:
        cancelled_visible = self.visible_ids
        self._pending.clear()
        self._visible.clear()
        return cancelled_visible
