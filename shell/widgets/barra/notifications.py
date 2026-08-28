"""Notifications button with unread badge and popup toggle."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import (
    NOTIFICATION_COMPACT_ICON_SIZE,
    NOTIFICATION_ICON_SIZE,
    NOTIFICATIONS_SOUND_ENABLED,
    NOTIFICATIONS_SOUND_PATH,
    NOTIFICATIONS_TOAST_ENABLED,
)
from ...eventbus import EventBus
from ...models import NotificationSnapshot
from ...popup_handle import PopupHandle, PopupOutsideDismiss, pointer_inside_widget
from ...servicios.notificaciones.notification_sound import play_notification_sound
from ...servicios.notificaciones.notifications import (
    NOTIFICATIONS_CHANGED,
    NOTIFICATION_RECEIVED,
    NOTIFICATIONS_PAUSED_CHANGED,
    NOTIFICATIONS_SOUND_MUTE_CHANGED,
    NotificationService,
)
from ...ui import ShellModule
from ..notificaciones.bell_icon import BellIcon
from ..notificaciones.notification_popup import NotificationPopup
from ..notificaciones.notification_toast_manager import NotificationToastManager


class NotificationsWidget(ShellModule):
    """Bar button that opens the notification popup and shows unread count."""

    def __init__(
        self,
        event_bus: EventBus,
        notification_service: NotificationService,
        shell_window: Gtk.Window,
    ) -> None:
        super().__init__("notifications-widget", spacing=0)

        self._event_bus = event_bus
        self._service = notification_service
        self._shell_window = shell_window
        self._compact = False
        self._shell_press_bound = False
        self._sound_path = (
            Path(__file__).resolve().parent.parent / NOTIFICATIONS_SOUND_PATH
        )

        self._overlay = Gtk.Overlay()
        self._button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._button.get_style_context().add_class("notifications-button")
        self._icon = BellIcon(NOTIFICATION_ICON_SIZE)
        self._button.add(self._icon)
        self._button.connect("clicked", self._on_button_clicked)

        self._badge = Gtk.Label(label="0")
        self._badge.get_style_context().add_class("notifications-badge")
        self._badge.set_halign(Gtk.Align.END)
        self._badge.set_valign(Gtk.Align.START)
        self._badge.set_margin_top(0)
        self._badge.set_margin_end(2)
        self._badge.set_no_show_all(True)
        self._badge.hide()

        self._overlay.add(self._button)
        self._overlay.add_overlay(self._badge)
        self.pack_start(self._overlay, False, False, 0)

        self._popup = PopupHandle(self._create_popup)
        self._toast_manager = NotificationToastManager(
            shell_window,
            notification_service,
            self._button,
            on_invoke_action=self._invoke_action,
            on_mark_read=self._mark_read,
        )
        self._outside_click = PopupOutsideDismiss()
        self._group_window: Gtk.Window | None = None

        self._event_bus.subscribe(NOTIFICATIONS_CHANGED, self._on_notifications_changed)
        self._event_bus.subscribe(NOTIFICATION_RECEIVED, self._on_notification_received)
        self._event_bus.subscribe(NOTIFICATIONS_PAUSED_CHANGED, self._on_paused_changed)
        self._event_bus.subscribe(NOTIFICATIONS_SOUND_MUTE_CHANGED, self._on_sound_mute_changed)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._sync_badge)

    def apply_shell_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._icon.set_pixel_size(
            NOTIFICATION_COMPACT_ICON_SIZE if compact else NOTIFICATION_ICON_SIZE
        )

    def _create_popup(self) -> NotificationPopup:
        return NotificationPopup(
            self._shell_window,
            self._service,
            on_mark_read=self._mark_read,
            on_mark_all_read=self._mark_all_read,
            on_dismiss=self._dismiss,
            on_clear_all=self._clear_all,
            on_invoke_action=self._invoke_action,
            on_open_app=self._open_app,
            on_open_group_window=self._open_group_window,
            on_mark_group_read=self._mark_group_read,
            on_dismiss_group=self._dismiss_group,
            on_toggle_paused=self._toggle_paused,
            on_toggle_app_sound_mute=self._toggle_app_sound_mute,
        )

    def _on_destroy(self, *_args) -> None:
        self._icon.stop()
        self._event_bus.unsubscribe(NOTIFICATIONS_CHANGED, self._on_notifications_changed)
        self._event_bus.unsubscribe(NOTIFICATION_RECEIVED, self._on_notification_received)
        self._event_bus.unsubscribe(NOTIFICATIONS_PAUSED_CHANGED, self._on_paused_changed)
        self._event_bus.unsubscribe(NOTIFICATIONS_SOUND_MUTE_CHANGED, self._on_sound_mute_changed)
        self.close_popup()
        self._close_group_window()
        self._toast_manager.destroy()

    def _on_notifications_changed(self, _snapshots: object) -> None:
        GLib.idle_add(self._handle_notifications_changed)

    def _on_notification_received(self, snapshot: NotificationSnapshot) -> None:
        GLib.idle_add(self._handle_notification_received, snapshot)

    def _on_paused_changed(self, _paused: object) -> None:
        GLib.idle_add(self._handle_paused_changed)

    def _on_sound_mute_changed(self, _apps: object) -> None:
        GLib.idle_add(self._handle_sound_mute_changed)

    def _handle_sound_mute_changed(self) -> bool:
        popup = self._popup.maybe
        if popup is not None and popup.get_visible():
            popup.refresh()
        return False

    def _handle_notifications_changed(self) -> bool:
        self._sync_badge()
        popup = self._popup.maybe
        if popup is not None and popup.get_visible():
            popup.refresh()
        return False

    def _handle_notification_received(self, snapshot: NotificationSnapshot) -> bool:
        self._sync_badge()
        if not self._service.paused:
            self._animate_bell()
            if NOTIFICATIONS_SOUND_ENABLED and self._service.should_play_sound(snapshot):
                play_notification_sound(self._sound_path, enabled=True)
            if NOTIFICATIONS_TOAST_ENABLED:
                self._toast_manager.enqueue(snapshot)
        return False

    def _handle_paused_changed(self) -> bool:
        self._sync_button_paused_style()
        popup = self._popup.maybe
        if popup is not None and popup.get_visible():
            popup.refresh()
        if self._service.paused:
            self._toast_manager.clear_presentations()
        return False

    def _sync_badge(self) -> bool:
        unread = self._service.unread_count
        if unread <= 0:
            self._badge.hide()
        else:
            self._badge.set_text(str(unread) if unread <= 99 else "99+")
            self._badge.show()
        self._sync_button_paused_style()
        return False

    def _sync_button_paused_style(self) -> None:
        style = self._button.get_style_context()
        if self._service.paused:
            style.add_class("notifications-button-paused")
        else:
            style.remove_class("notifications-button-paused")

    def _animate_bell(self) -> None:
        self._icon.ring()

    def _ensure_shell_press_handler(self) -> None:
        if self._shell_press_bound:
            return
        self._shell_window.connect("button-press-event", self._on_shell_button_press)
        self._shell_press_bound = True

    def _on_button_clicked(self, *_args) -> None:
        if self._popup.is_visible():
            self.close_popup()
            return

        self._toast_manager.clear_presentations()
        self._ensure_shell_press_handler()
        popup = self._popup.get()
        popup.open_for(self._button)
        extra_windows = ()
        if self._group_window is not None:
            extra_windows = (self._group_window,)
        self._outside_click.install(
            popup,
            self._shell_window,
            (self._button,),
            self.close_popup,
            self._event_bus,
            extra_windows=extra_windows,
        )

    def close_popup(self) -> None:
        self._outside_click.uninstall()
        self._close_group_window()
        popup = self._popup.maybe
        if popup is not None:
            popup.close_popup()

    def _mark_read(self, notification_id: int) -> None:
        self._service.mark_read(notification_id)

    def _mark_group_read(self, snapshots: list[NotificationSnapshot]) -> None:
        for snapshot in snapshots:
            self._service.mark_read(snapshot.id)

    def _mark_all_read(self) -> None:
        self._service.mark_all_read()

    def _dismiss(self, notification_id: int) -> None:
        self._service.dismiss(notification_id)

    def _dismiss_group(self, snapshots: list[NotificationSnapshot]) -> None:
        for snapshot in snapshots:
            self._service.dismiss(snapshot.id)

    def _clear_all(self) -> None:
        self._service.dismiss_all()

    def _invoke_action(self, notification_id: int, action_key: str) -> None:
        self._service.invoke_action(notification_id, action_key)

    def _open_app(self, snapshot: NotificationSnapshot) -> None:
        from ..notificaciones.notification_popup import open_notification_app
        open_notification_app(snapshot)

    def _open_group_window(
        self,
        group_snapshots: list[NotificationSnapshot],
        anchor: Gtk.Widget,
        popup: Gtk.Window,
    ) -> None:
        from ..notificaciones.notification_group_window import NotificationGroupWindow

        if self._group_window is not None:
            self._group_window.hide_group()

        window = NotificationGroupWindow(
            self._shell_window,
            self._service,
            group_snapshots,
            on_invoke_action=self._invoke_action,
            on_dismiss=self._dismiss,
            on_open_app=self._open_app,
            anchor=anchor,
            popup_window=popup,
            notifications_position=popup.position,
        )
        self._group_window = window
        self._outside_click.set_extra_windows((window,))
        window.present_group()

    def _close_group_window(self) -> None:
        if self._group_window is not None:
            self._group_window.hide_group()
            self._group_window = None

    def _toggle_paused(self) -> None:
        self._service.toggle_paused()

    def _toggle_app_sound_mute(self, app_key: str) -> None:
        self._service.toggle_app_sound_muted(app_key)

    def _on_shell_button_press(self, _window: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button not in (1, 3):
            return False
        if not self._popup.is_visible():
            return False

        popup = self._popup.maybe
        if popup is None:
            return False

        if pointer_inside_widget(self._button) or popup.pointer_is_inside():
            return False
        if self._group_window is not None and pointer_inside_window(self._group_window):
            return False

        self.close_popup()
        return False
