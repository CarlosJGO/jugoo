"""Transient incoming notification toast card anchored below the bell button."""

from __future__ import annotations

from typing import Callable, Literal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, GLib, Pango

from ...config import (
    NOTIFICATION_POPUP_ICON_SIZE,
    NOTIFICATIONS_TOAST_WIDTH,
)
from ...models import NotificationSnapshot
from ...popup_handle import is_pointer_leaving_surface, pointer_inside_widget
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.notification_icon import apply_notification_icon

ToastDismissReason = Literal["timeout", "click", "cancel"]

_URGENCY_LABELS = {0: "Baja", 2: "Urgente"}


class NotificationToast(Gtk.EventBox):
    """Clickable toast card widget hosted by the shared notification layer."""

    def __init__(
        self,
        notification_service: NotificationService,
        *,
        on_invoke_action: Callable[[int, str], None],
        on_dismiss: Callable[[NotificationSnapshot, ToastDismissReason], None],
    ) -> None:
        super().__init__()

        self._service = notification_service
        self._on_invoke_action = on_invoke_action
        self._on_dismiss = on_dismiss
        self._snapshot: NotificationSnapshot | None = None
        self._hide_source_id = 0

        self.set_name("shell-notification-toast")
        self.set_can_focus(False)
        self.set_focus_on_click(False)
        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )

        self.connect("button-press-event", self._on_card_clicked)
        self.connect("enter-notify-event", self._on_enter_notify)
        self.connect("leave-notify-event", self._on_leave_notify)

        # Outer card container
        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._card.set_size_request(NOTIFICATIONS_TOAST_WIDTH, -1)
        card_style = self._card.get_style_context()
        card_style.add_class("notification-toast-content")
        card_style.add_class("notification-toast-card")
        self.add(self._card)

        # Header: Icon + App title / Urgency + Close button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("notification-toast-header")

        icon_slot = Gtk.Box()
        icon_slot.set_size_request(
            NOTIFICATION_POPUP_ICON_SIZE + 4,
            NOTIFICATION_POPUP_ICON_SIZE + 4,
        )
        self._icon = Gtk.Image()
        self._icon.get_style_context().add_class("notification-toast-icon")
        self._icon.set_halign(Gtk.Align.CENTER)
        self._icon.set_valign(Gtk.Align.CENTER)
        icon_slot.pack_start(self._icon, True, True, 0)
        header.pack_start(icon_slot, False, False, 0)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        meta_box.set_hexpand(True)

        self._app_label = Gtk.Label(xalign=0)
        self._app_label.get_style_context().add_class("notification-toast-app")
        meta_box.pack_start(self._app_label, False, False, 0)

        self._urgency_badge = Gtk.Label(xalign=0)
        self._urgency_badge.get_style_context().add_class("notification-toast-urgency")
        self._urgency_badge.set_no_show_all(True)
        meta_box.pack_start(self._urgency_badge, False, False, 0)
        header.pack_start(meta_box, True, True, 0)

        self._close_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._close_button.set_tooltip_text("Cerrar")
        self._close_button.get_style_context().add_class("notification-toast-close")
        self._close_button.add(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        )
        self._close_button.connect("clicked", self._on_close_clicked)
        header.pack_start(self._close_button, False, False, 0)
        self._card.pack_start(header, False, False, 0)

        # Body: Summary + Detail text
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content_box.get_style_context().add_class("notification-toast-body-box")

        self._summary = Gtk.Label(xalign=0)
        self._summary.get_style_context().add_class("notification-toast-summary")
        self._summary.set_hexpand(True)
        self._summary.set_line_wrap(True)
        self._summary.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._summary.set_lines(2)
        self._summary.set_ellipsize(Pango.EllipsizeMode.END)
        content_box.pack_start(self._summary, False, False, 0)

        self._body = Gtk.Label(xalign=0)
        self._body.get_style_context().add_class("notification-toast-body")
        self._body.set_hexpand(True)
        self._body.set_line_wrap(True)
        self._body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._body.set_lines(4)
        self._body.set_ellipsize(Pango.EllipsizeMode.END)
        content_box.pack_start(self._body, False, False, 0)
        self._card.pack_start(content_box, False, False, 0)

        # Actions row (if notification contains actions)
        self._actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._actions_box.get_style_context().add_class("notification-toast-actions")
        self._actions_box.set_no_show_all(True)
        self._card.pack_start(self._actions_box, False, False, 0)

    @property
    def snapshot(self) -> NotificationSnapshot | None:
        return self._snapshot

    def show_notification(
        self,
        snapshot: NotificationSnapshot,
    ) -> None:
        """Bind content and show this widget inside the shared layer."""
        self._cancel_hide_timer()
        self._snapshot = snapshot
        style = self._card.get_style_context()
        style.remove_class("notification-toast-critical")
        style.remove_class("notification-toast-low")

        urgency_style = self._urgency_badge.get_style_context()
        urgency_style.remove_class("notification-toast-urgency-critical")
        urgency_style.remove_class("notification-toast-urgency-low")

        if snapshot.urgency == 2:
            style.add_class("notification-toast-critical")
            urgency_style.add_class("notification-toast-urgency-critical")
        elif snapshot.urgency == 0:
            style.add_class("notification-toast-low")
            urgency_style.add_class("notification-toast-urgency-low")

        apply_notification_icon(
            self._icon,
            snapshot,
            pixel_size=NOTIFICATION_POPUP_ICON_SIZE,
        )

        urgency_text = _URGENCY_LABELS.get(snapshot.urgency)
        self._app_label.set_text(snapshot.app_name or "Notificación")
        if urgency_text:
            self._urgency_badge.set_text(urgency_text)
            self._urgency_badge.show()
        else:
            self._urgency_badge.hide()

        self._summary.set_text(snapshot.summary or "Notificación")
        if snapshot.body:
            self._body.set_text(snapshot.body)
            self._body.show()
        else:
            self._body.hide()

        # Render action buttons
        for child in self._actions_box.get_children():
            self._actions_box.remove(child)

        action_list = [a for a in snapshot.actions if a.key != "default"]
        if action_list:
            for action in action_list:
                btn = Gtk.Button(label=action.label, relief=Gtk.ReliefStyle.NONE)
                btn.get_style_context().add_class("notification-toast-action-btn")
                btn.connect(
                    "clicked",
                    lambda _btn, key=action.key: self._on_action_clicked(key),
                )
                self._actions_box.pack_start(btn, False, False, 0)
            self._actions_box.set_no_show_all(False)
            self._actions_box.show_all()
        else:
            self._actions_box.set_no_show_all(True)
            self._actions_box.hide()

        self.show_all()
        timeout_ms = self._service.resolve_display_timeout_ms(snapshot)
        if timeout_ms > 0:
            self._hide_source_id = GLib.timeout_add(timeout_ms, self._auto_hide)

    def dismiss(self, reason: ToastDismissReason, *, emit: bool = True) -> None:
        snapshot = self._snapshot
        self._cancel_hide_timer()
        self._snapshot = None
        self.hide()
        if emit and snapshot is not None:
            self._on_dismiss(snapshot, reason)

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
        return False

    def _on_leave_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if not is_pointer_leaving_surface(event):
            return False
        if pointer_inside_widget(self):
            return False
        if self._snapshot is not None:
            timeout_ms = self._service.resolve_display_timeout_ms(self._snapshot)
            if timeout_ms > 0:
                self._hide_source_id = GLib.timeout_add(timeout_ms, self._auto_hide)
        return False

    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        self.dismiss("click")

    def _on_action_clicked(self, action_key: str) -> None:
        if self._snapshot is not None:
            self._on_invoke_action(self._snapshot.id, action_key)
        self.dismiss("click")

    def _on_card_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1 or self._snapshot is None:
            return False

        # Don't intercept clicks that originated on child buttons
        target = event.widget
        while target is not None and target != self:
            if isinstance(target, Gtk.Button):
                return False
            target = target.get_parent()

        snapshot = self._snapshot
        default = next(
            (action.key for action in snapshot.actions if action.key == "default"),
            None,
        )
        if default is None and snapshot.actions:
            default = snapshot.actions[0].key
        if default is not None:
            self._on_invoke_action(snapshot.id, default)
        self.dismiss("click")
        return True
