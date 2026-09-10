"""Presents at most one Jugoo assistant card (replaces, never stacks)."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...models import NotificationSnapshot
from ...servicios.notificaciones.notifications import NotificationService
from .assistant_card import AssistantCard, AssistantDismissReason
from .assistant_layer import AssistantLayer


class AssistantPresenter:
    """Owns the assistant layer and the single live card."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        notification_service: NotificationService,
        *,
        on_mark_read: Callable[[int], None] | None = None,
    ) -> None:
        self._service = notification_service
        self._on_mark_read = on_mark_read
        self._layer = AssistantLayer(shell_window)
        self._card = AssistantCard(
            notification_service,
            on_dismiss=self._handle_dismiss,
        )
        self._layer.set_card(self._card)
        self._active_id: int | None = None

    def present(self, snapshot: NotificationSnapshot) -> None:
        """Show or replace the assistant card with ``snapshot``."""
        try:
            previous_id = self._active_id
            if previous_id is not None and previous_id != snapshot.id:
                current = self._service.get(previous_id)
                if current is not None and not current.expired and not current.dismissed:
                    self._service.expire(previous_id)

            self._active_id = snapshot.id
            self._card.show_assistant(snapshot)
            if not self._layer.get_visible():
                self._layer.show_layer()
            else:
                self._layer.refresh_position()
        except Exception as error:
            print(f"shell: assistant presentation failed: {error}", flush=True)
            try:
                self.clear()
            except Exception:
                pass

    def clear(self) -> None:
        """Hide the assistant without marking history dismissed."""
        if self._active_id is None and not self._layer.get_visible():
            return
        self._card.dismiss("cancel", emit=False)
        self._active_id = None
        self._layer.hide_layer()

    def destroy(self) -> None:
        self.clear()
        self._layer.destroy()

    def _handle_dismiss(
        self,
        snapshot: NotificationSnapshot,
        reason: AssistantDismissReason,
    ) -> None:
        if self._active_id == snapshot.id:
            self._active_id = None
        self._layer.hide_layer()

        if reason == "cancel":
            return
        if reason == "timeout":
            self._service.expire(snapshot.id)
            return
        if reason == "click":
            current = self._service.get(snapshot.id)
            if current is not None and not current.read and self._on_mark_read is not None:
                self._on_mark_read(snapshot.id)
