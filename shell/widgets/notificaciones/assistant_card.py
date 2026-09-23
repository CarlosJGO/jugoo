"""Assistant briefing / chat card — Jugoo speaking, not a system toast."""

from __future__ import annotations

from typing import Callable, Literal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, GLib, Pango

from ...config import (
    ASSISTANT_CARD_MAX_HEIGHT,
    ASSISTANT_CARD_MESSAGE_MAX_HEIGHT,
    ASSISTANT_CARD_WIDTH,
    ASSISTANT_ICON_SIZE,
)
from ...identity import APPLICATION_NAME
from ...models import NotificationSnapshot, assistant_source_label
from ...popup_handle import is_pointer_leaving_surface, pointer_inside_widget
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.notification_icon import apply_notification_icon
from ...ui.starfield import install_starfield, resolve_event_bus

AssistantDismissReason = Literal["timeout", "click", "cancel"]


class AssistantCard(Gtk.EventBox):
    """Chat-like assistant surface: who is speaking, scrollable message, origin."""

    def __init__(
        self,
        notification_service: NotificationService,
        *,
        on_dismiss: Callable[[NotificationSnapshot, AssistantDismissReason], None],
    ) -> None:
        super().__init__()

        self._service = notification_service
        self._on_dismiss = on_dismiss
        self._snapshot: NotificationSnapshot | None = None
        self._hide_source_id = 0

        self.set_name("shell-assistant-card")
        self.set_can_focus(False)
        self.set_focus_on_click(False)
        self.set_size_request(ASSISTANT_CARD_WIDTH, -1)
        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )
        self.connect("button-press-event", self._on_card_clicked)
        self.connect("enter-notify-event", self._on_enter_notify)
        self.connect("leave-notify-event", self._on_leave_notify)

        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._card.set_size_request(ASSISTANT_CARD_WIDTH, -1)
        self._card.get_style_context().add_class("assistant-card")
        self._card.get_style_context().add_class("assistant-card-content")
        install_starfield(self, self._card, resolve_event_bus(self))

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.get_style_context().add_class("assistant-card-header")

        icon_slot = Gtk.Box()
        icon_slot.set_size_request(ASSISTANT_ICON_SIZE + 4, ASSISTANT_ICON_SIZE + 4)
        self._icon = Gtk.Image()
        self._icon.get_style_context().add_class("assistant-card-icon")
        self._icon.set_halign(Gtk.Align.CENTER)
        self._icon.set_valign(Gtk.Align.CENTER)
        icon_slot.pack_start(self._icon, True, True, 0)
        header.pack_start(icon_slot, False, False, 0)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_valign(Gtk.Align.CENTER)
        self._title = Gtk.Label(xalign=0)
        self._title.get_style_context().add_class("assistant-card-title")
        self._title.set_text(APPLICATION_NAME)
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        self._title.set_single_line_mode(True)
        titles.pack_start(self._title, False, False, 0)

        self._subtitle = Gtk.Label(xalign=0)
        self._subtitle.get_style_context().add_class("assistant-card-subtitle")
        self._subtitle.set_text("te está hablando")
        self._subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self._subtitle.set_single_line_mode(True)
        titles.pack_start(self._subtitle, False, False, 0)
        header.pack_start(titles, True, True, 0)
        self._card.pack_start(header, False, False, 0)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        bubble.get_style_context().add_class("assistant-card-bubble")

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self._scroll.set_hexpand(True)
        self._scroll.set_min_content_height(48)
        self._scroll.set_max_content_height(ASSISTANT_CARD_MESSAGE_MAX_HEIGHT)
        try:
            self._scroll.set_propagate_natural_height(True)
        except AttributeError:
            pass

        self._message = Gtk.TextView()
        self._message.get_style_context().add_class("assistant-card-message")
        self._message.set_editable(False)
        self._message.set_cursor_visible(False)
        self._message.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._message.set_accepts_tab(False)
        self._message.set_left_margin(2)
        self._message.set_right_margin(2)
        self._message.set_top_margin(2)
        self._message.set_bottom_margin(2)
        self._message.set_pixels_above_lines(1)
        self._message.set_pixels_below_lines(1)
        self._message.set_can_focus(False)
        self._scroll.add(self._message)
        bubble.pack_start(self._scroll, True, True, 0)
        self._card.pack_start(bubble, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.get_style_context().add_class("assistant-card-footer")

        self._meta = Gtk.Label(xalign=0)
        self._meta.get_style_context().add_class("assistant-card-meta")
        self._meta.set_ellipsize(Pango.EllipsizeMode.END)
        self._meta.set_single_line_mode(True)
        self._meta.set_halign(Gtk.Align.START)
        self._meta.set_hexpand(True)
        self._meta.set_no_show_all(True)
        self._meta.hide()
        footer.pack_start(self._meta, True, True, 0)

        self._origin = Gtk.Label(xalign=1)
        self._origin.get_style_context().add_class("assistant-card-origin")
        self._origin.set_ellipsize(Pango.EllipsizeMode.END)
        self._origin.set_single_line_mode(True)
        self._origin.set_halign(Gtk.Align.END)
        footer.pack_end(self._origin, False, False, 0)
        self._card.pack_start(footer, False, False, 0)

    def do_get_preferred_height(self):
        minimum, natural = Gtk.EventBox.do_get_preferred_height(self)
        cap = ASSISTANT_CARD_MAX_HEIGHT
        return min(minimum, cap), min(natural, cap)

    def do_get_preferred_height_for_width(self, width: int):
        minimum, natural = Gtk.EventBox.do_get_preferred_height_for_width(self, width)
        cap = ASSISTANT_CARD_MAX_HEIGHT
        return min(minimum, cap), min(natural, cap)

    @property
    def snapshot(self) -> NotificationSnapshot | None:
        return self._snapshot

    def show_assistant(self, snapshot: NotificationSnapshot) -> None:
        """Bind content and (re)start the auto-hide timer."""
        self._cancel_hide_timer()
        self._snapshot = snapshot

        apply_notification_icon(
            self._icon,
            snapshot,
            pixel_size=ASSISTANT_ICON_SIZE,
        )
        self._title.set_text(snapshot.app_name.strip() or APPLICATION_NAME)

        message = (snapshot.body or snapshot.summary or "").strip()
        buffer = self._message.get_buffer()
        buffer.set_text(message)

        meta = (snapshot.meta or "").strip()
        # "te responde" is already the subtitle; don't duplicate in the footer.
        if meta and meta.casefold() not in {"te responde", "te está hablando"}:
            self._subtitle.set_text(meta)
            self._meta.hide()
        else:
            self._subtitle.set_text("te está hablando")
            self._meta.hide()

        self._origin.set_text(assistant_source_label(snapshot.source))

        self.show_all()
        self._meta.hide()
        timeout_ms = self._service.resolve_display_timeout_ms(snapshot)
        if timeout_ms > 0:
            self._hide_source_id = GLib.timeout_add(timeout_ms, self._auto_hide)

    def dismiss(self, reason: AssistantDismissReason, *, emit: bool = True) -> None:
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

    def _restart_hide_timer(self) -> None:
        self._cancel_hide_timer()
        snapshot = self._snapshot
        if snapshot is None:
            return
        timeout_ms = self._service.resolve_display_timeout_ms(snapshot)
        if timeout_ms > 0:
            self._hide_source_id = GLib.timeout_add(timeout_ms, self._auto_hide)

    def _on_card_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.dismiss("click")
        return True

    def _on_enter_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_hide_timer()
        return False

    def _on_leave_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if is_pointer_leaving_surface(self, event):
            self._restart_hide_timer()
        return False
