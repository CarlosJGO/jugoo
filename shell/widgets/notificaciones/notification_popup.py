"""Notification history popup anchored below the bar button."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk, GLib, Pango

from ...config import (
    NOTIFICATION_POPUP_ICON_SIZE,
    NOTIFICATION_POPUP_LIST_SPACING,
    NOTIFICATION_POPUP_MAX_HEIGHT,
    NOTIFICATION_POPUP_OFFSET,
    NOTIFICATION_POPUP_ROW_BODY_LINES,
    NOTIFICATION_POPUP_WIDTH,
)
from ...models import NotificationSnapshot
from ...popup_handle import pointer_inside_widget, present_popup, hide_popup
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.notification_icon import apply_notification_icon
from ...window_identity import (
    TITLE_NOTIFICATIONS,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)

_URGENCY_LABELS = {
    0: "Baja",
    1: "Normal",
    2: "Urgente",
}


def open_notification_app(snapshot: NotificationSnapshot) -> None:
    desktop = snapshot.desktop_entry.strip() if snapshot else ""
    if desktop:
        stem = Path(desktop).stem
        if stem:
            try:
                subprocess.Popen(["gtk-launch", stem])
                return
            except (OSError, subprocess.SubprocessError):
                pass
    app_name = snapshot.app_name.strip() if snapshot else ""
    if app_name:
        try:
            subprocess.Popen(["gtk-launch", app_name])
        except (OSError, subprocess.SubprocessError):
            pass


class NotificationItemRow(Gtk.EventBox):
    """Single notification entry inside the popup list."""

    def __init__(
        self,
        snapshot: NotificationSnapshot,
        *,
        app_key: str,
        app_sound_muted: bool,
        on_mark_read: Callable[[int], None],
        on_dismiss: Callable[[int], None],
        on_invoke_action: Callable[[int, str], None],
        on_toggle_app_sound_mute: Callable[[str], None],
    ) -> None:
        super().__init__()

        self._snapshot = snapshot
        self._on_invoke_action = on_invoke_action
        self._default_action = next(
            (action.key for action in snapshot.actions if action.key == "default"),
            snapshot.actions[0].key if snapshot.actions else None,
        )
        if self._default_action is not None:
            self.connect("button-press-event", self._on_row_clicked)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        style = content.get_style_context()
        style.add_class("notification-item")
        if not snapshot.read:
            style.add_class("notification-item-unread")
        if snapshot.expired:
            style.add_class("notification-item-expired")
        if snapshot.urgency == 2:
            style.add_class("notification-item-critical")
        elif snapshot.urgency == 0:
            style.add_class("notification-item-low")
        self.add(content)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_slot = Gtk.Box()
        icon_slot.set_size_request(
            NOTIFICATION_POPUP_ICON_SIZE + 4,
            NOTIFICATION_POPUP_ICON_SIZE + 4,
        )
        icon = Gtk.Image()
        apply_notification_icon(
            icon,
            snapshot,
            pixel_size=NOTIFICATION_POPUP_ICON_SIZE,
        )
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon.get_style_context().add_class("notification-item-icon")
        icon_slot.pack_start(icon, True, True, 0)
        header.pack_start(icon_slot, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        meta.set_hexpand(True)
        app_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        app_label = Gtk.Label(xalign=0)
        app_label.get_style_context().add_class("notification-item-app")
        app_label.set_markup(
            f"<b>{_escape_markup(snapshot.app_name)}</b>  "
            f"<span alpha='70%'>{_escape_markup(_format_timestamp(snapshot.timestamp))}</span>"
        )
        app_line.pack_start(app_label, True, True, 0)
        if snapshot.urgency in _URGENCY_LABELS:
            urgency = Gtk.Label(label=_URGENCY_LABELS[snapshot.urgency])
            urgency.get_style_context().add_class("notification-item-urgency")
            if snapshot.urgency == 0:
                urgency.get_style_context().add_class("notification-item-urgency-low")
            elif snapshot.urgency == 2:
                urgency.get_style_context().add_class("notification-item-urgency-critical")
            app_line.pack_start(urgency, False, False, 0)
        meta.pack_start(app_line, False, False, 0)
        header.pack_start(meta, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sound_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        sound_button.get_style_context().add_class("notification-item-action")
        sound_icon = (
            "audio-volume-muted-symbolic"
            if app_sound_muted
            else "audio-volume-high-symbolic"
        )
        sound_button.set_tooltip_text(
            "Activar sonido de la aplicación"
            if app_sound_muted
            else "Silenciar sonido de la aplicación"
        )
        sound_button.add(Gtk.Image.new_from_icon_name(sound_icon, Gtk.IconSize.MENU))
        sound_button.connect(
            "clicked",
            lambda _btn, key=app_key: on_toggle_app_sound_mute(key),
        )
        actions.pack_start(sound_button, False, False, 0)

        if not snapshot.read:
            read_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            read_button.get_style_context().add_class("notification-item-action")
            read_button.set_tooltip_text("Marcar como leída")
            read_button.add(
                Gtk.Image.new_from_icon_name("mail-read-symbolic", Gtk.IconSize.MENU)
            )
            read_button.connect(
                "clicked",
                lambda _btn, nid=snapshot.id: on_mark_read(nid),
            )
            actions.pack_start(read_button, False, False, 0)

        dismiss_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        dismiss_button.set_tooltip_text("Eliminar")
        dismiss_button.get_style_context().add_class("notification-item-action")
        dismiss_button.add(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        )
        dismiss_button.connect(
            "clicked",
            lambda _btn, nid=snapshot.id: on_dismiss(nid),
        )
        actions.pack_start(dismiss_button, False, False, 0)
        header.pack_start(actions, False, False, 0)
        content.pack_start(header, False, False, 0)

        if snapshot.summary:
            summary = Gtk.Label(label=snapshot.summary, xalign=0)
            summary.get_style_context().add_class("notification-item-summary")
            summary.set_hexpand(True)
            summary.set_single_line_mode(True)
            summary.set_ellipsize(Pango.EllipsizeMode.END)
            content.pack_start(summary, False, False, 0)

        if snapshot.body:
            body = Gtk.Label(label=snapshot.body, xalign=0)
            body.get_style_context().add_class("notification-item-body")
            body.set_hexpand(True)
            body.set_line_wrap(True)
            body.set_lines(NOTIFICATION_POPUP_ROW_BODY_LINES)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            content.pack_start(body, False, False, 0)

        if snapshot.actions:
            action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            action_row.get_style_context().add_class("notification-item-actions")
            for action in snapshot.actions:
                button = Gtk.Button(label=action.label, relief=Gtk.ReliefStyle.NONE)
                button.get_style_context().add_class("notification-item-action-btn")
                button.connect(
                    "clicked",
                    lambda _btn, nid=snapshot.id, key=action.key: on_invoke_action(nid, key),
                )
                action_row.pack_start(button, False, False, 0)
            content.pack_start(action_row, False, False, 0)

    def _on_row_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1 or self._default_action is None:
            return False
        target = event.widget
        while target is not None:
            if isinstance(target, Gtk.Button):
                return False
            target = target.get_parent()
        self._on_invoke_action(self._snapshot.id, self._default_action)
        return True


class NotificationPopup(Gtk.Window):
    """Scrollable notification list positioned under the bar button."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        notification_service: NotificationService,
        *,
        on_mark_read: Callable[[int], None],
        on_mark_all_read: Callable[[], None],
        on_dismiss: Callable[[int], None],
        on_clear_all: Callable[[], None],
        on_invoke_action: Callable[[int, str], None],
        on_open_app: Callable[[NotificationSnapshot], None],
        on_toggle_paused: Callable[[], None],
        on_toggle_app_sound_mute: Callable[[str], None],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._service = notification_service
        self._on_mark_read = on_mark_read
        self._on_mark_all_read = on_mark_all_read
        self._on_dismiss = on_dismiss
        self._on_clear_all = on_clear_all
        self._on_invoke_action = on_invoke_action
        self._on_open_app = on_open_app
        self._on_toggle_paused = on_toggle_paused
        self._on_toggle_app_sound_mute = on_toggle_app_sound_mute
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None

        self.set_name("shell-notifications")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATIONS)
        configure_interactive_popup(self)
        self.set_default_size(NOTIFICATION_POPUP_WIDTH, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_size_request(NOTIFICATION_POPUP_WIDTH, -1)
        outer.get_style_context().add_class("notification-popup-content")
        self.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header.get_style_context().add_class("notification-popup-header")
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._title = Gtk.Label(label="Notificaciones", xalign=0)
        self._title.get_style_context().add_class("notification-popup-title")
        title_row.pack_start(self._title, True, True, 0)

        self._pause_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._pause_button.get_style_context().add_class("notification-popup-clear")
        self._pause_button.connect("clicked", lambda _btn: self._on_toggle_paused())
        title_row.pack_start(self._pause_button, False, False, 0)
        header.pack_start(title_row, False, False, 0)

        self._paused_banner = Gtk.Label(xalign=0)
        self._paused_banner.get_style_context().add_class("notification-popup-paused")
        self._paused_banner.set_no_show_all(True)
        header.pack_start(self._paused_banner, False, False, 0)

        self._muted_apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._muted_apps_box.get_style_context().add_class("notification-popup-muted-apps")
        self._muted_apps_box.set_no_show_all(True)
        header.pack_start(self._muted_apps_box, False, False, 0)

        actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions_row.set_halign(Gtk.Align.END)
        mark_all = Gtk.Button(label="Marcar todas leídas", relief=Gtk.ReliefStyle.NONE)
        mark_all.get_style_context().add_class("notification-popup-clear")
        mark_all.connect("clicked", lambda _btn: self._on_mark_all_read())
        actions_row.pack_start(mark_all, False, False, 0)

        clear_all = Gtk.Button(label="Limpiar todo", relief=Gtk.ReliefStyle.NONE)
        clear_all.get_style_context().add_class("notification-popup-clear")
        clear_all.connect("clicked", lambda _btn: self._on_clear_all())
        actions_row.pack_start(clear_all, False, False, 0)
        header.pack_start(actions_row, False, False, 0)
        outer.pack_start(header, False, False, 0)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_propagate_natural_height(True)
        self._scrolled.set_max_content_height(NOTIFICATION_POPUP_MAX_HEIGHT)
        self._scrolled.get_style_context().add_class("notification-popup-scroll")
        outer.pack_start(self._scrolled, False, False, 0)

        self._list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=NOTIFICATION_POPUP_LIST_SPACING,
        )
        self._list_box.get_style_context().add_class("notification-popup-list")
        self._scrolled.add(self._list_box)

        self._empty_label = Gtk.Label(label="No hay notificaciones")
        self._empty_label.get_style_context().add_class("notification-popup-empty")
        self._empty_label.set_margin_top(12)
        self._empty_label.set_margin_bottom(12)

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self.refresh()
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor_button = None
        self._fixed_popup_top = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self) -> None:
        self._sync_paused_ui()
        self._sync_muted_apps_ui()
        for child in self._list_box.get_children():
            self._list_box.remove(child)

        snapshots = tuple(
            sorted(
                self._service.history_snapshots,
                key=lambda item: item.timestamp,
                reverse=True,
            )
        )
        if not snapshots:
            self._list_box.pack_start(self._empty_label, False, False, 0)
            self._empty_label.show_all()
            self._scrolled.queue_resize()
            if self.get_visible() and self._anchor_button is not None:
                schedule_popup_position(self._position_after_show)
            return

        self._empty_label.hide()

        groups: dict[str, list[NotificationSnapshot]] = {}
        for snapshot in snapshots:
            key = snapshot.app_name.casefold().strip() or "application"
            groups.setdefault(key, []).append(snapshot)

        grouped_snapshots = sorted(
            groups.values(),
            key=lambda group: group[0].timestamp,
            reverse=True,
        )

        for group in grouped_snapshots:
            group.sort(key=lambda item: item.timestamp, reverse=True)
            representative = group[0]
            app_key = self._service.app_key_for(representative)
            row = NotificationGroupRow(
                group,
                app_key=app_key,
                app_sound_muted=self._service.is_app_sound_muted(app_key),
                on_mark_read=self._on_mark_read,
                on_dismiss=self._on_dismiss,
                on_invoke_action=self._on_invoke_action,
                on_open_app=self._on_open_app,
                on_toggle_app_sound_mute=self._on_toggle_app_sound_mute,
            )
            self._list_box.pack_start(row, False, False, 0)

        self._list_box.show_all()
        self._scrolled.queue_resize()
        self._list_box.get_parent().queue_resize()
        if self.get_visible() and self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def _sync_paused_ui(self) -> None:
        paused = self._service.paused
        if paused:
            self._title.set_text("Notificaciones (pausadas)")
            self._paused_banner.set_text(
                "Modo pausa activo: se guardan notificaciones sin sonido ni avisos."
            )
            self._paused_banner.show()
            self._pause_button.set_label("Reanudar")
            self._pause_button.set_tooltip_text("Desactivar pausa")
        else:
            self._title.set_text("Notificaciones")
            self._paused_banner.hide()
            self._pause_button.set_label("Pausar")
            self._pause_button.set_tooltip_text("Activar pausa (Do Not Disturb)")

    def _sync_muted_apps_ui(self) -> None:
        for child in self._muted_apps_box.get_children():
            self._muted_apps_box.remove(child)

        muted_apps = self._service.sound_muted_apps
        if not muted_apps:
            self._muted_apps_box.set_no_show_all(True)
            self._muted_apps_box.hide()
            return

        title = Gtk.Label(label="Sonido silenciado", xalign=0)
        title.get_style_context().add_class("notification-popup-muted-title")
        self._muted_apps_box.pack_start(title, False, False, 0)

        for app_key in muted_apps:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=app_key, xalign=0)
            label.get_style_context().add_class("notification-popup-muted-app")
            label.set_hexpand(True)
            row.pack_start(label, True, True, 0)

            unmute = Gtk.Button(label="Activar sonido", relief=Gtk.ReliefStyle.NONE)
            unmute.get_style_context().add_class("notification-popup-clear")
            unmute.connect(
                "clicked",
                lambda _btn, key=app_key: self._on_toggle_app_sound_mute(key),
            )
            row.pack_start(unmute, False, False, 0)
            self._muted_apps_box.pack_start(row, False, False, 0)

        self._muted_apps_box.set_no_show_all(False)
        self._muted_apps_box.show_all()

    def _position_after_show(self) -> bool:
        if self._anchor_button is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor_button,
                title=TITLE_NOTIFICATIONS,
                offset=NOTIFICATION_POPUP_OFFSET,
                fixed_top=self._fixed_popup_top,
            )
            if self._fixed_popup_top is None and top is not None:
                self._fixed_popup_top = top
        return False


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

        timestamp = Gtk.Label(
            label=_format_timestamp(snapshot.timestamp),
            xalign=0,
        )
        timestamp.get_style_context().add_class("notification-mini-row-time")
        text_box.pack_start(timestamp, False, False, 0)

        row.pack_start(text_box, True, True, 0)
        self.add(row)


class NotificationGroupRow(Gtk.EventBox):
    """Grouped notification entry inside the popup list."""

    def __init__(
        self,
        group_snapshots: list[NotificationSnapshot],
        *,
        app_key: str,
        app_sound_muted: bool,
        on_mark_read: Callable[[int], None],
        on_dismiss: Callable[[int], None],
        on_invoke_action: Callable[[int, str], None],
        on_open_app: Callable[[NotificationSnapshot], None],
        on_toggle_app_sound_mute: Callable[[str], None],
    ) -> None:
        super().__init__()

        self._group_snapshots = group_snapshots
        self._representative = group_snapshots[0]
        self._on_invoke_action = on_invoke_action
        self._on_open_app = on_open_app
        self._default_action = next(
            (
                action.key
                for action in self._representative.actions
                if action.key == "default"
            ),
            self._representative.actions[0].key
            if self._representative.actions
            else None,
        )
        self._popover: Gtk.Popover | None = None
        self._popover_leave_timeout_id = 0

        self.connect("enter-notify-event", self._on_enter_notify)
        self.connect("leave-notify-event", self._on_leave_notify)
        if self._default_action is not None or self._representative.desktop_entry:
            self.connect("button-press-event", self._on_row_clicked)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.get_style_context().add_class("notification-group-row")
        if not self._representative.read:
            content.get_style_context().add_class("notification-group-row-unread")
        if self._representative.urgency == 2:
            content.get_style_context().add_class("notification-group-row-critical")
        elif self._representative.urgency == 0:
            content.get_style_context().add_class("notification-group-row-low")
        self.add(content)

        icon_slot = Gtk.Box()
        icon_slot.set_size_request(
            NOTIFICATION_POPUP_ICON_SIZE + 4,
            NOTIFICATION_POPUP_ICON_SIZE + 4,
        )
        icon = Gtk.Image()
        apply_notification_icon(
            icon,
            self._representative,
            pixel_size=NOTIFICATION_POPUP_ICON_SIZE,
        )
        icon.get_style_context().add_class("notification-group-row-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon_slot.pack_start(icon, True, True, 0)
        content.pack_start(icon_slot, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        meta.set_hexpand(True)
        app_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        app_label = Gtk.Label(xalign=0)
        app_label.get_style_context().add_class("notification-group-row-app")
        app_label.set_markup(
            f"<b>{_escape_markup(self._representative.app_name)}</b>  "
            f"<span alpha='70%'>{_escape_markup(_format_timestamp(self._representative.timestamp))}</span>"
        )
        app_line.pack_start(app_label, True, True, 0)
        if self._representative.urgency in _URGENCY_LABELS:
            urgency = Gtk.Label(label=_URGENCY_LABELS[self._representative.urgency])
            urgency.get_style_context().add_class("notification-group-row-urgency")
            if self._representative.urgency == 0:
                urgency.get_style_context().add_class("notification-group-row-urgency-low")
            elif self._representative.urgency == 2:
                urgency.get_style_context().add_class("notification-group-row-urgency-critical")
            app_line.pack_start(urgency, False, False, 0)
        meta.pack_start(app_line, False, False, 0)

        if self._representative.summary:
            summary = Gtk.Label(
                label=self._representative.summary,
                xalign=0,
            )
            summary.get_style_context().add_class("notification-group-row-summary")
            summary.set_hexpand(True)
            summary.set_single_line_mode(True)
            summary.set_ellipsize(Pango.EllipsizeMode.END)
            meta.pack_start(summary, False, False, 0)

        content.pack_start(meta, True, True, 0)

        if len(self._group_snapshots) > 1:
            badge = Gtk.Label(label=str(len(self._group_snapshots)))
            badge.get_style_context().add_class("notification-group-row-badge")
            content.pack_start(badge, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sound_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        sound_button.get_style_context().add_class("notification-item-action")
        sound_icon = (
            "audio-volume-muted-symbolic"
            if app_sound_muted
            else "audio-volume-high-symbolic"
        )
        sound_button.set_tooltip_text(
            "Activar sonido de la aplicación"
            if app_sound_muted
            else "Silenciar sonido de la aplicación"
        )
        sound_button.add(Gtk.Image.new_from_icon_name(sound_icon, Gtk.IconSize.MENU))
        sound_button.connect(
            "clicked",
            lambda _btn, key=app_key: on_toggle_app_sound_mute(key),
        )
        actions.pack_start(sound_button, False, False, 0)

        if not self._representative.read:
            read_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            read_button.get_style_context().add_class("notification-item-action")
            read_button.set_tooltip_text("Marcar como leída")
            read_button.add(
                Gtk.Image.new_from_icon_name("mail-read-symbolic", Gtk.IconSize.MENU)
            )
            read_button.connect(
                "clicked",
                lambda _btn, nid=self._representative.id: on_mark_read(nid),
            )
            actions.pack_start(read_button, False, False, 0)

        dismiss_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        dismiss_button.set_tooltip_text("Eliminar")
        dismiss_button.get_style_context().add_class("notification-item-action")
        dismiss_button.add(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        )
        dismiss_button.connect(
            "clicked",
            lambda _btn, nid=self._representative.id: on_dismiss(nid),
        )
        actions.pack_start(dismiss_button, False, False, 0)
        content.pack_start(actions, False, False, 0)

    def _on_enter_notify(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self._cancel_leave_timeout()
        if len(self._group_snapshots) > 1:
            self._show_popover()
        return False

    def _on_leave_notify(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        if self._popover is not None and self._popover.get_visible():
            self._popover_leave_timeout_id = GLib.timeout_add(150, self._leave_popover)
        else:
            self._cancel_leave_timeout()
        return False

    def _on_row_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        target = event.widget
        while target is not None and target != self:
            if isinstance(target, Gtk.Button):
                return False
            target = target.get_parent()

        snapshot = self._representative
        if self._default_action is not None:
            self._on_invoke_action(snapshot.id, self._default_action)
        else:
            self._on_open_app(snapshot)
        return True

    def _show_popover(self) -> None:
        if self._popover is not None:
            self._popover.show_all()
            return

        popover = Gtk.Popover(relative_to=self)
        popover.get_style_context().add_class("notification-group-popover")
        popover.set_position(Gtk.PositionType.RIGHT)
        popover.set_modal(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(6)
        for snapshot in self._group_snapshots:
            box.pack_start(NotificationMiniRow(snapshot), False, False, 0)
        popover.add(box)

        popover.connect("leave-notify-event", lambda _w, _e: self._schedule_leave_popover())
        popover.connect("enter-notify-event", lambda _w, _e: self._cancel_leave_timeout() or False)
        popover.connect("closed", lambda _w: self._on_popover_closed())

        self._popover = popover
        popover.show_all()

    def _schedule_leave_popover(self) -> bool:
        self._popover_leave_timeout_id = GLib.timeout_add(150, self._leave_popover)
        return False

    def _leave_popover(self) -> bool:
        if self._popover is not None:
            self._popover.hide()
        self._popover_leave_timeout_id = 0
        return False

    def _cancel_leave_timeout(self) -> None:
        if self._popover_leave_timeout_id:
            GLib.source_remove(self._popover_leave_timeout_id)
            self._popover_leave_timeout_id = 0

    def _on_popover_closed(self) -> None:
        self._popover = None
        self._popover_leave_timeout_id = 0


def _format_timestamp(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp)
    now = datetime.now()
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    return moment.strftime("%d/%m %H:%M")


def _escape_markup(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
