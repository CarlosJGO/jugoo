"""Queued toast presentation with a bounded number of visible windows."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import NOTIFICATIONS_MAX_VISIBLE_TOASTS
from ...models import NotificationSnapshot
from ...servicios.notificaciones.notifications import NotificationService
from .notification_toast import NotificationToast, ToastDismissReason
from .notification_toast_layer import NotificationToastLayer
from .toast_presentation_queue import ToastPresentationQueue


class NotificationToastManager:
    """Owns toast slots, the pending FIFO queue, and promotion logic."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        notification_service: NotificationService,
        anchor_button: Gtk.Widget,
        *,
        on_invoke_action: Callable[[int, str], None],
        on_mark_read: Callable[[int], None],
        max_visible: int = NOTIFICATIONS_MAX_VISIBLE_TOASTS,
    ) -> None:
        self._shell_window = shell_window
        self._service = notification_service
        self._anchor_button = anchor_button
        self._on_invoke_action = on_invoke_action
        self._on_mark_read = on_mark_read
        self._queue = ToastPresentationQueue(max_visible)
        self._toasts: dict[int, NotificationToast] = {}
        self._snapshots: dict[int, NotificationSnapshot] = {}
        self._layer = NotificationToastLayer(shell_window)

    @property
    def pending_count(self) -> int:
        return self._queue.pending_count()

    @property
    def visible_count(self) -> int:
        return self._queue.visible_count()

    @property
    def pending_notification_ids(self) -> tuple[int, ...]:
        return self._queue.pending_ids

    @property
    def visible_notification_ids(self) -> tuple[int, ...]:
        return self._queue.visible_ids

    def enqueue(self, snapshot: NotificationSnapshot) -> None:
        self._snapshots[snapshot.id] = snapshot
        if snapshot.id in self._toasts:
            # If already visible, update existing card in place
            self._toasts[snapshot.id].show_notification(snapshot)
            return
        self._queue.enqueue(snapshot.id)
        self._promote()

    def clear_presentations(self) -> None:
        """Hide visible toasts and drop the pending queue without touching history."""
        self._queue.clear()
        self._snapshots.clear()
        for toast in list(self._toasts.values()):
            toast.dismiss("cancel", emit=False)
            toast.destroy()
        self._toasts.clear()
        self._layer.clear_toasts()
        self._layer.hide_layer()

    def destroy(self) -> None:
        self.clear_presentations()
        self._layer.destroy()

    def _promote(self) -> None:
        for _slot, notification_id in self._queue.promote():
            snapshot = self._resolve_snapshot(notification_id)
            if snapshot is None:
                self._queue.release(notification_id)
                continue
            if notification_id in self._toasts:
                toast = self._toasts[notification_id]
            else:
                toast = NotificationToast(
                    self._service,
                    on_invoke_action=self._on_invoke_action,
                    on_dismiss=self._handle_toast_dismiss,
                )
                self._toasts[notification_id] = toast
                self._layer.add_toast(toast)
            toast.show_notification(snapshot)
        if self._queue.visible_count():
            self._layer.show_for(self._anchor_button)

    def _resolve_snapshot(self, notification_id: int) -> NotificationSnapshot | None:
        cached = self._snapshots.get(notification_id)
        if cached is not None:
            return cached
        for snapshot in self._service.snapshots:
            if snapshot.id == notification_id:
                return snapshot
        return None

    def _handle_toast_dismiss(
        self,
        snapshot: NotificationSnapshot,
        reason: ToastDismissReason,
    ) -> None:
        toast = self._toasts.pop(snapshot.id, None)
        if toast is not None:
            self._layer.remove_toast(toast)
            toast.destroy()

        if reason == "cancel":
            self._queue.release(snapshot.id)
            self._snapshots.pop(snapshot.id, None)
            return

        self._queue.release(snapshot.id)
        self._snapshots.pop(snapshot.id, None)

        if reason == "timeout":
            self._service.expire(snapshot.id)
        elif reason == "click":
            current = self._service.get(snapshot.id)
            if current is not None and not current.read:
                if not current.actions:
                    self._on_mark_read(snapshot.id)

        self._promote()
        if self._queue.visible_count() == 0:
            self._layer.hide_layer()
