"""Fullscreen whisper: a tiny HUD chip, distinct from the normal toast card."""

from __future__ import annotations

from typing import Callable, Literal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, GLib, Pango

from ...config import (
    NOTIFICATIONS_WHISPER_CRITICAL_TIMEOUT_MS,
    NOTIFICATIONS_WHISPER_ICON_SIZE,
    NOTIFICATIONS_WHISPER_MAX_HEIGHT,
    NOTIFICATIONS_WHISPER_TIMEOUT_MS,
    NOTIFICATIONS_WHISPER_WIDTH,
)
from ...models import NotificationSnapshot
from ...popup_handle import is_pointer_leaving_surface, pointer_inside_widget
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.notification_icon import apply_notification_icon

WhisperDismissReason = Literal["timeout", "click", "cancel"]


def format_whisper_chip_text(snapshot: NotificationSnapshot) -> tuple[str, str]:
    """Return ``(title, message_snippet)`` for the compact HUD chip.

    Title is summary/app; message is a collapsed body preview when it adds
    information beyond the title.
    """
    summary = " ".join((snapshot.summary or "").split())
    body = " ".join((snapshot.body or "").split())
    app = " ".join((snapshot.app_name or "").split())
    title = summary or app or "Notificación"
    if not body:
        return title, ""
    if body.casefold() == title.casefold():
        return title, ""
    if summary and body.casefold().startswith(summary.casefold()):
        # Body already carries the summary (e.g. "Alice: hola") — show body as message.
        return (app or title), body
    return title, body


class NotificationWhisper(Gtk.EventBox):
    """Single-line capsule: icon + title + message snippet. Minimal fullscreen intrusion."""

    def __init__(
        self,
        notification_service: NotificationService,
        *,
        on_invoke_action: Callable[[int, str], None],
        on_dismiss: Callable[[NotificationSnapshot, WhisperDismissReason], None],
        on_open_app: Callable[[NotificationSnapshot], None] | None = None,
    ) -> None:
        super().__init__()

        self._service = notification_service
        self._on_invoke_action = on_invoke_action
        self._on_dismiss = on_dismiss
        self._on_open_app = on_open_app
        self._snapshot: NotificationSnapshot | None = None
        self._hide_source_id = 0

        self.set_name("shell-notification-whisper")
        self.set_can_focus(False)
        self.set_focus_on_click(False)
        self.set_size_request(NOTIFICATIONS_WHISPER_WIDTH, NOTIFICATIONS_WHISPER_MAX_HEIGHT)
        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )
        self.connect("button-press-event", self._on_chip_clicked)
        self.connect("enter-notify-event", self._on_enter_notify)
        self.connect("leave-notify-event", self._on_leave_notify)

        self._chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._chip.set_size_request(NOTIFICATIONS_WHISPER_WIDTH, NOTIFICATIONS_WHISPER_MAX_HEIGHT)
        self._chip.set_valign(Gtk.Align.CENTER)
        chip_style = self._chip.get_style_context()
        chip_style.add_class("notification-whisper-chip")

        self._accent = Gtk.Box()
        self._accent.set_size_request(3, 16)
        self._accent.set_valign(Gtk.Align.CENTER)
        self._accent.get_style_context().add_class("notification-whisper-accent")
        self._chip.pack_start(self._accent, False, False, 0)

        self._icon = Gtk.Image()
        self._icon.get_style_context().add_class("notification-whisper-icon")
        self._icon.set_halign(Gtk.Align.CENTER)
        self._icon.set_valign(Gtk.Align.CENTER)
        self._icon.set_size_request(
            NOTIFICATIONS_WHISPER_ICON_SIZE,
            NOTIFICATIONS_WHISPER_ICON_SIZE,
        )
        self._chip.pack_start(self._icon, False, False, 0)

        self._text = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._text.set_hexpand(True)
        self._text.set_valign(Gtk.Align.CENTER)

        self._title = Gtk.Label(xalign=0)
        self._title.get_style_context().add_class("notification-whisper-summary")
        self._title.set_single_line_mode(True)
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        self._title.set_max_width_chars(18)
        self._title.set_valign(Gtk.Align.CENTER)
        self._text.pack_start(self._title, False, False, 0)

        self._sep = Gtk.Label(label="·")
        self._sep.get_style_context().add_class("notification-whisper-sep")
        self._sep.set_valign(Gtk.Align.CENTER)
        self._text.pack_start(self._sep, False, False, 0)

        self._message = Gtk.Label(xalign=0)
        self._message.get_style_context().add_class("notification-whisper-message")
        self._message.set_hexpand(True)
        self._message.set_single_line_mode(True)
        self._message.set_ellipsize(Pango.EllipsizeMode.END)
        self._message.set_valign(Gtk.Align.CENTER)
        self._text.pack_start(self._message, True, True, 0)

        self._chip.pack_start(self._text, True, True, 0)
        self.add(self._chip)

    def do_get_preferred_height(self):
        cap = NOTIFICATIONS_WHISPER_MAX_HEIGHT
        return cap, cap

    def do_get_preferred_height_for_width(self, _width: int):
        cap = NOTIFICATIONS_WHISPER_MAX_HEIGHT
        return cap, cap

    def do_get_preferred_width(self):
        minimum, natural = Gtk.EventBox.do_get_preferred_width(self)
        cap = NOTIFICATIONS_WHISPER_WIDTH
        return min(minimum, cap), min(natural, cap)

    @property
    def snapshot(self) -> NotificationSnapshot | None:
        return self._snapshot

    def show_notification(self, snapshot: NotificationSnapshot) -> None:
        """Bind content and show this whisper chip inside the shared layer."""
        self._cancel_hide_timer()
        self._snapshot = snapshot
        style = self._chip.get_style_context()
        style.remove_class("notification-whisper-critical")
        style.remove_class("notification-whisper-low")
        style.remove_class("notification-whisper-hover")

        if snapshot.urgency == 2:
            style.add_class("notification-whisper-critical")
        elif snapshot.urgency == 0:
            style.add_class("notification-whisper-low")

        apply_notification_icon(
            self._icon,
            snapshot,
            pixel_size=NOTIFICATIONS_WHISPER_ICON_SIZE,
        )

        title, message = format_whisper_chip_text(snapshot)
        self._title.set_text(title)
        if message:
            self._message.set_text(message)
            self._sep.show()
            self._message.show()
        else:
            self._message.set_text("")
            self._sep.hide()
            self._message.hide()

        tip_parts = [p for p in (snapshot.app_name, snapshot.summary, snapshot.body) if p]
        tips: list[str] = []
        for part in tip_parts:
            if not tips or tips[-1] != part:
                tips.append(part)
        self.set_tooltip_text("\n".join(tips) if tips else title)

        self.show_all()
        if not message:
            self._sep.hide()
            self._message.hide()
        timeout_ms = self._resolve_timeout_ms(snapshot)
        if timeout_ms > 0:
            self._hide_source_id = GLib.timeout_add(timeout_ms, self._auto_hide)

    def dismiss(self, reason: WhisperDismissReason, *, emit: bool = True) -> None:
        snapshot = self._snapshot
        self._cancel_hide_timer()
        self._snapshot = None
        self._chip.get_style_context().remove_class("notification-whisper-hover")
        self.hide()
        if emit and snapshot is not None:
            self._on_dismiss(snapshot, reason)

    def _resolve_timeout_ms(self, snapshot: NotificationSnapshot) -> int:
        base = self._service.resolve_display_timeout_ms(snapshot)
        cap = (
            NOTIFICATIONS_WHISPER_CRITICAL_TIMEOUT_MS
            if snapshot.urgency == 2
            else NOTIFICATIONS_WHISPER_TIMEOUT_MS
        )
        if base <= 0:
            return cap
        return min(base, cap)

    def _auto_hide(self) -> bool:
        self._hide_source_id = 0
        if pointer_inside_widget(self):
            return False
        self.dismiss("timeout")
        return False

    def _cancel_hide_timer(self) -> None:
        if self._hide_source_id:
            GLib.source_remove(self._hide_source_id)
            self._hide_source_id = 0

    def _on_enter_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        mode = getattr(event, "mode", None)
        if mode in (Gdk.CrossingMode.GRAB, Gdk.CrossingMode.UNGRAB):
            return False
        self._cancel_hide_timer()
        self._chip.get_style_context().add_class("notification-whisper-hover")
        return False

    def _on_leave_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if not is_pointer_leaving_surface(event):
            return False
        if pointer_inside_widget(self):
            return False
        self._chip.get_style_context().remove_class("notification-whisper-hover")
        if self._snapshot is not None:
            timeout_ms = self._resolve_timeout_ms(self._snapshot)
            if timeout_ms > 0:
                self._hide_source_id = GLib.timeout_add(timeout_ms, self._auto_hide)
        return False

    def _on_chip_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1 or self._snapshot is None:
            return False

        snapshot = self._snapshot
        default = next(
            (action.key for action in snapshot.actions if action.key == "default"),
            None,
        )
        if default is None and snapshot.actions:
            default = snapshot.actions[0].key
        if default is not None:
            self._on_invoke_action(snapshot.id, default)
        if self._on_open_app is not None:
            self._on_open_app(snapshot)
        self.dismiss("click")
        return True
