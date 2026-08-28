"""Compact read-only row shown inside the group hover popover."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Pango

from ...config import NOTIFICATION_POPUP_ICON_SIZE
from ...models import NotificationSnapshot
from ...ui.notification_icon import apply_notification_icon

if TYPE_CHECKING:
    from .notification_popup import _format_timestamp


class NotificationMiniRow(Gtk.EventBox):
    """Compact read-only row shown inside the group hover popover."""

    def __init__(self, snapshot: NotificationSnapshot) -> None:
        super().__init__()
        self._snapshot = snapshot

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("notification-mini-row")

        icon = Gtk.Image()
        apply_notification_icon(
            icon,
            snapshot,
            pixel_size=NOTIFICATION_POPUP_ICON_SIZE,
        )
        icon.get_style_context().add_class("notification-mini-row-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        row.pack_start(icon, False, False, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_box.set_hexpand(True)

        if snapshot.summary:
            summary = Gtk.Label(label=snapshot.summary, xalign=0)
            summary.get_style_context().add_class("notification-mini-row-summary")
            summary.set_hexpand(True)
            summary.set_single_line_mode(True)
            summary.set_ellipsize(Pango.EllipsizeMode.END)
            text_box.pack_start(summary, False, False, 0)

        if snapshot.body:
            body = Gtk.Label(label=snapshot.body, xalign=0)
            body.get_style_context().add_class("notification-mini-row-body")
            body.set_hexpand(True)
            body.set_line_wrap(True)
            body.set_lines(2)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            text_box.pack_start(body, False, False, 0)

        from .notification_popup import _format_timestamp

        timestamp = Gtk.Label(
            label=_format_timestamp(snapshot.timestamp),
            xalign=0,
        )
        timestamp.get_style_context().add_class("notification-mini-row-time")
        text_box.pack_start(timestamp, False, False, 0)

        row.pack_start(text_box, True, True, 0)
        self.add(row)
