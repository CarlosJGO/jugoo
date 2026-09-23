"""Notification history popup anchored below the bar button."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ...config import (
    NOTIFICATION_POPUP_ICON_SIZE,
    NOTIFICATION_POPUP_LIST_SPACING,
    NOTIFICATION_POPUP_MAX_HEIGHT,
    NOTIFICATION_POPUP_OFFSET,
    NOTIFICATION_POPUP_REVEAL_STAGGER_MS,
    NOTIFICATION_POPUP_ROW_APPEAR_MS,
    NOTIFICATION_POPUP_ROW_BODY_LINES,
    NOTIFICATION_POPUP_ROW_SLIDE_PX,
    NOTIFICATION_POPUP_WIDTH,
)
from ...models import NotificationSnapshot
from ...popup_handle import (
    hide_popup,
    is_pointer_leaving_surface,
    pointer_inside_widget,
    present_popup,
)
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.disfraces import WindowRole, dress_window
from ...ui.notification_icon import apply_notification_icon
from ...ui.theme import active_theme
from ...window_identity import (
    TITLE_NOTIFICATIONS,
    compute_popup_top_left,
    configure_interactive_popup,
    configure_toplevel,
    popup_window_size,
    register_shell_popup,
    schedule_popup_position,
    monitor_containing_point,
    anchor_button_geometry,
    reposition_popup,
)
from ...popup_spawn import publish_popup_spawn
from .notification_grouping import group_notification_snapshots

_URGENCY_LABELS = {
    0: "Baja",
    1: "Normal",
    2: "Urgente",
}
_ROW_APPEAR_TICK_MS = 16


def _ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4.0 * t * t * t
    u = -2.0 * t + 2.0
    return 1.0 - (u * u * u) / 2.0


class NotificationItemRow(Gtk.EventBox):
    """Single notification entry inside the popup list."""

    def __init__(
        self,
        snapshot: NotificationSnapshot,
        *,
        app_key: str,
        app_sound_muted: bool,
        app_blocked: bool,
        on_mark_read: Callable[[int], None],
        on_dismiss: Callable[[int], None],
        on_invoke_action: Callable[[int, str], None],
        on_toggle_app_sound_mute: Callable[[str], None],
        on_toggle_app_blocked: Callable[[str], None],
    ) -> None:
        super().__init__()

        self._snapshot = snapshot
        self._on_invoke_action = on_invoke_action
        self._default_action = next(
            (action.key for action in snapshot.actions if action.key == "default"),
            snapshot.actions[0].key if snapshot.actions else None,
        )
        if self._default_action is not None or self._snapshot.desktop_entry:
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

        block_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        block_button.get_style_context().add_class("notification-item-action")
        block_icon = (
            "notifications-disabled-symbolic"
            if app_blocked
            else "notifications-symbolic"
        )
        block_button.set_tooltip_text(
            "Permitir notificaciones de la aplicación"
            if app_blocked
            else "Bloquear notificaciones de la aplicación"
        )
        block_button.add(Gtk.Image.new_from_icon_name(block_icon, Gtk.IconSize.MENU))
        block_button.connect(
            "clicked",
            lambda _btn, key=app_key: on_toggle_app_blocked(key),
        )
        actions.pack_start(block_button, False, False, 0)

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
        if event.button != 1:
            return False
        target = event.widget
        while target is not None:
            if isinstance(target, Gtk.Button):
                return False
            target = target.get_parent()
        if self._default_action is not None:
            self._on_invoke_action(self._snapshot.id, self._default_action)
        self._on_open_app(self._snapshot)
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
        on_open_group_window: Callable[
            [list[NotificationSnapshot], Gtk.Widget, Gtk.Window], None
        ],
        on_mark_group_read: Callable[[list[NotificationSnapshot]], None],
        on_dismiss_group: Callable[[list[NotificationSnapshot]], None],
        on_toggle_paused: Callable[[], None],
        on_toggle_app_sound_mute: Callable[[str], None],
        on_toggle_app_blocked: Callable[[str], None],
        on_preload_group_pages: Callable[
            [list[list[NotificationSnapshot]]], None
        ]
        | None = None,
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
        self._on_open_group_window = on_open_group_window
        self._on_mark_group_read = on_mark_group_read
        self._on_dismiss_group = on_dismiss_group
        self._on_toggle_paused = on_toggle_paused
        self._on_toggle_app_sound_mute = on_toggle_app_sound_mute
        self._on_toggle_app_blocked = on_toggle_app_blocked
        self._on_preload_group_pages = on_preload_group_pages
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._position: tuple[int, int, int] | None = None
        self._pending_groups: list[list[NotificationSnapshot]] = []
        self._reveal_index = 0
        self._reveal_source_id = 0

        self.set_name("shell-notifications")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATIONS)
        configure_interactive_popup(self)
        self.set_default_size(NOTIFICATION_POPUP_WIDTH, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_size_request(NOTIFICATION_POPUP_WIDTH, -1)
        outer.get_style_context().add_class("notification-popup-content")
        dress_window(self, WindowRole.NOTIFICATIONS_POPUP, outer)

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

        self._blocked_apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._blocked_apps_box.get_style_context().add_class("notification-popup-muted-apps")
        self._blocked_apps_box.set_no_show_all(True)
        header.pack_start(self._blocked_apps_box, False, False, 0)

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
        self._cancel_progressive_reveal()
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._position = None
        # Chrome first (header + empty list) so the window is never blank/invisible
        # while packages are materialised.
        self._prepare_open_shell()
        top = publish_popup_spawn(
            self,
            anchor_button,
            title=TITLE_NOTIFICATIONS,
            offset=NOTIFICATION_POPUP_OFFSET,
        )
        if top is not None:
            self._fixed_popup_top = top
            present_popup(self, fade=False)
            GLib.idle_add(self._position_after_show)
        else:
            present_popup(self, fade=False)
            schedule_popup_position(self._position_after_show)
        GLib.idle_add(self._start_progressive_reveal)

    def close_popup(self) -> None:
        self._cancel_progressive_reveal()
        self._anchor_button = None
        self._fixed_popup_top = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    @property
    def position(self) -> tuple[int, int, int] | None:
        return self._position

    def refresh(self) -> None:
        """Rebuild the list synchronously (live updates while the panel is open)."""
        self._cancel_progressive_reveal()
        self._sync_paused_ui()
        self._sync_muted_apps_ui()
        self._sync_blocked_apps_ui()
        self._clear_list_children()

        snapshots = self._sorted_history()
        if not snapshots:
            self._show_empty_state()
            if self._on_preload_group_pages is not None:
                self._on_preload_group_pages([])
            self._scrolled.queue_resize()
            if self.get_visible() and self._anchor_button is not None:
                schedule_popup_position(self._position_after_show)
            return

        self._empty_label.hide()
        groups = list(group_notification_snapshots(snapshots))
        for group in groups:
            row = self._build_group_row(group)
            self._list_box.pack_start(row, False, False, 0)

        if self._on_preload_group_pages is not None:
            self._on_preload_group_pages([group for group in groups if len(group) > 1])

        self._list_box.show_all()
        self._scrolled.queue_resize()
        self._list_box.get_parent().queue_resize()
        if self.get_visible() and self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def _prepare_open_shell(self) -> None:
        self._sync_paused_ui()
        self._sync_muted_apps_ui()
        self._sync_blocked_apps_ui()
        self._clear_list_children()
        if not self._service.history_snapshots:
            self._show_empty_state()
        else:
            self._empty_label.hide()

    def _sorted_history(self) -> tuple[NotificationSnapshot, ...]:
        return tuple(
            sorted(
                self._service.history_snapshots,
                key=lambda item: item.timestamp,
                reverse=True,
            )
        )

    def _clear_list_children(self) -> None:
        for child in list(self._list_box.get_children()):
            self._list_box.remove(child)

    def _show_empty_state(self) -> None:
        if self._empty_label.get_parent() is None:
            self._list_box.pack_start(self._empty_label, False, False, 0)
        self._empty_label.show_all()

    def _build_group_row(
        self,
        group: list[NotificationSnapshot],
    ) -> NotificationGroupRow:
        representative = group[0]
        app_key = self._service.app_key_for(representative)
        return NotificationGroupRow(
            group,
            app_key=app_key,
            app_sound_muted=self._service.is_app_sound_muted(app_key),
            app_blocked=self._service.is_app_blocked(app_key),
            on_mark_group_read=self._on_mark_group_read,
            on_dismiss_group=self._on_dismiss_group,
            on_invoke_action=self._on_invoke_action,
            on_open_app=self._on_open_app,
            on_toggle_app_sound_mute=self._on_toggle_app_sound_mute,
            on_toggle_app_blocked=self._on_toggle_app_blocked,
            on_open_group_window=self._on_open_group_window,
        )

    def _cancel_progressive_reveal(self) -> None:
        if self._reveal_source_id:
            GLib.source_remove(self._reveal_source_id)
            self._reveal_source_id = 0
        self._pending_groups = []
        self._reveal_index = 0

    def _start_progressive_reveal(self) -> bool:
        if self._anchor_button is None:
            return False
        snapshots = self._sorted_history()
        if not snapshots:
            self._show_empty_state()
            if self._on_preload_group_pages is not None:
                self._on_preload_group_pages([])
            self._scrolled.queue_resize()
            schedule_popup_position(self._position_after_show)
            return False

        if self._empty_label.get_parent() is not None:
            self._list_box.remove(self._empty_label)
        self._empty_label.hide()
        self._pending_groups = list(group_notification_snapshots(snapshots))
        self._reveal_index = 0
        # First package immediately; remaining on a stagger so they stack in.
        self._reveal_next_group()
        if self._reveal_index < len(self._pending_groups):
            self._reveal_source_id = GLib.timeout_add(
                NOTIFICATION_POPUP_REVEAL_STAGGER_MS,
                self._reveal_next_group,
            )
        return False

    def _reveal_next_group(self) -> bool:
        if self._anchor_button is None:
            self._reveal_source_id = 0
            return False
        if self._reveal_index >= len(self._pending_groups):
            self._reveal_source_id = 0
            self._finish_progressive_reveal()
            return False

        group = self._pending_groups[self._reveal_index]
        self._reveal_index += 1
        row = self._build_group_row(group)
        row.set_opacity(0.0)
        row.set_margin_top(-NOTIFICATION_POPUP_ROW_SLIDE_PX)
        self._list_box.pack_start(row, False, False, 0)
        row.show_all()
        self._animate_row_appear(row)

        if self._reveal_index == 1 or self._reveal_index % 4 == 0:
            schedule_popup_position(self._position_after_show)

        if self._reveal_index >= len(self._pending_groups):
            self._reveal_source_id = 0
            self._finish_progressive_reveal()
            return False
        return True

    def _finish_progressive_reveal(self) -> None:
        groups = self._pending_groups
        self._pending_groups = []
        if self._on_preload_group_pages is not None:
            self._on_preload_group_pages([group for group in groups if len(group) > 1])
        self._scrolled.queue_resize()
        parent = self._list_box.get_parent()
        if parent is not None:
            parent.queue_resize()
        if self.get_visible() and self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def _animate_row_appear(self, row: Gtk.Widget) -> None:
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
            row.set_opacity(1.0)
            row.set_margin_top(0)
            return

        duration = NOTIFICATION_POPUP_ROW_APPEAR_MS
        if theme is not None and theme.animation.duration > 0:
            duration = min(duration, theme.animation.duration)
        step = min(1.0, _ROW_APPEAR_TICK_MS / max(duration, 1))
        setattr(row, "_appear_progress", 0.0)

        def _tick() -> bool:
            if self._anchor_button is None or not row.get_parent():
                return False
            progress = min(1.0, float(getattr(row, "_appear_progress", 0.0)) + step)
            setattr(row, "_appear_progress", progress)
            eased = _ease_in_out_cubic(progress)
            row.set_opacity(eased)
            row.set_margin_top(
                int(round(-NOTIFICATION_POPUP_ROW_SLIDE_PX * (1.0 - eased)))
            )
            if progress >= 1.0:
                row.set_opacity(1.0)
                row.set_margin_top(0)
                return False
            return True

        GLib.timeout_add(_ROW_APPEAR_TICK_MS, _tick)

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

    def _sync_blocked_apps_ui(self) -> None:
        for child in self._blocked_apps_box.get_children():
            self._blocked_apps_box.remove(child)

        blocked_apps = self._service.blocked_apps
        if not blocked_apps:
            self._blocked_apps_box.set_no_show_all(True)
            self._blocked_apps_box.hide()
            return

        title = Gtk.Label(label="Apps bloqueadas", xalign=0)
        title.get_style_context().add_class("notification-popup-muted-title")
        self._blocked_apps_box.pack_start(title, False, False, 0)

        for app_key in blocked_apps:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=app_key, xalign=0)
            label.get_style_context().add_class("notification-popup-muted-app")
            label.set_hexpand(True)
            row.pack_start(label, True, True, 0)

            unblock = Gtk.Button(label="Permitir", relief=Gtk.ReliefStyle.NONE)
            unblock.get_style_context().add_class("notification-popup-clear")
            unblock.connect(
                "clicked",
                lambda _btn, key=app_key: self._on_toggle_app_blocked(key),
            )
            row.pack_start(unblock, False, False, 0)
            self._blocked_apps_box.pack_start(row, False, False, 0)

        self._blocked_apps_box.set_no_show_all(False)
        self._blocked_apps_box.show_all()

    def _position_after_show(self) -> bool:
        if self._anchor_button is None:
            return False
        geometry = anchor_button_geometry(self._anchor_button)
        if geometry is None:
            return False
        width, height = popup_window_size(self)
        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        left, top = compute_popup_top_left(
            button_center_x=geometry.center_x,
            button_bottom=geometry.bottom,
            popup_width=width,
            popup_height=height,
            offset=NOTIFICATION_POPUP_OFFSET,
            fixed_top=self._fixed_popup_top,
            monitor=monitor,
        )
        self._position = (left, top, width)
        reposition_popup(self, title=TITLE_NOTIFICATIONS, x=left, y=top)
        if self._fixed_popup_top is None:
            self._fixed_popup_top = top
        return False


class NotificationGroupRow(Gtk.EventBox):
    """Grouped notification entry inside the popup list."""

    def __init__(
        self,
        group_snapshots: list[NotificationSnapshot],
        *,
        app_key: str,
        app_sound_muted: bool,
        app_blocked: bool,
        on_mark_group_read: Callable[[list[NotificationSnapshot]], None],
        on_dismiss_group: Callable[[list[NotificationSnapshot]], None],
        on_invoke_action: Callable[[int, str], None],
        on_open_app: Callable[[NotificationSnapshot], None],
        on_toggle_app_sound_mute: Callable[[str], None],
        on_toggle_app_blocked: Callable[[str], None],
        on_open_group_window: Callable[
            [list[NotificationSnapshot], Gtk.Widget, Gtk.Window], None
        ],
    ) -> None:
        super().__init__()

        self._group_snapshots = group_snapshots
        self._representative = group_snapshots[0]
        self._on_invoke_action = on_invoke_action
        self._on_open_app = on_open_app
        self._on_open_group_window = on_open_group_window
        self._on_mark_group_read = on_mark_group_read
        self._on_dismiss_group = on_dismiss_group
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
        self._hover_opened = False

        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("enter-notify-event", self._on_enter_notify)
        self.connect("leave-notify-event", self._on_leave_notify)
        self.connect("motion-notify-event", self._on_motion_notify)
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

        self._face = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._face.set_hexpand(True)
        self._tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._tools.set_hexpand(True)
        self._tools.get_style_context().add_class("notification-group-row-tools")

        self._stack = Gtk.Stack()
        self._stack.set_homogeneous(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(120)
        self._stack.set_hexpand(True)
        self._stack.add_named(self._face, "face")
        self._stack.add_named(self._tools, "tools")
        self._stack.set_visible_child_name("face")

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
        self._face.pack_start(icon_slot, False, False, 0)

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

        self._face.pack_start(meta, True, True, 0)

        if len(self._group_snapshots) > 1:
            badge = Gtk.Label(label=str(len(self._group_snapshots)))
            badge.get_style_context().add_class("notification-group-row-badge")
            self._face.pack_start(badge, False, False, 0)

        gear_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        gear_button.get_style_context().add_class("notification-item-action")
        gear_button.set_tooltip_text("Opciones")
        gear_button.add(
            Gtk.Image.new_from_icon_name("preferences-system-symbolic", Gtk.IconSize.MENU)
        )
        gear_button.connect("clicked", self._on_gear_clicked)
        self._face.pack_start(gear_button, False, False, 0)

        tools_label = Gtk.Label(label="Opciones", xalign=0)
        tools_label.get_style_context().add_class("notification-group-row-tools-label")
        tools_label.set_hexpand(True)
        self._tools.pack_start(tools_label, True, True, 0)

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
        self._tools.pack_start(sound_button, False, False, 0)

        block_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        block_button.get_style_context().add_class("notification-item-action")
        block_icon = (
            "notifications-disabled-symbolic"
            if app_blocked
            else "notifications-symbolic"
        )
        block_button.set_tooltip_text(
            "Permitir notificaciones de la aplicación"
            if app_blocked
            else "Bloquear notificaciones de la aplicación"
        )
        block_button.add(Gtk.Image.new_from_icon_name(block_icon, Gtk.IconSize.MENU))
        block_button.connect(
            "clicked",
            lambda _btn, key=app_key: on_toggle_app_blocked(key),
        )
        self._tools.pack_start(block_button, False, False, 0)

        if not self._representative.read:
            read_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            read_button.get_style_context().add_class("notification-item-action")
            read_button.set_tooltip_text("Marcar como leída")
            read_button.add(
                Gtk.Image.new_from_icon_name("mail-read-symbolic", Gtk.IconSize.MENU)
            )
            read_button.connect(
                "clicked",
                lambda _btn: on_mark_group_read(group_snapshots),
            )
            self._tools.pack_start(read_button, False, False, 0)

        dismiss_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        dismiss_button.set_tooltip_text("Eliminar")
        dismiss_button.get_style_context().add_class("notification-item-action")
        dismiss_button.add(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        )
        dismiss_button.connect(
            "clicked",
            lambda _btn: on_dismiss_group(group_snapshots),
        )
        self._tools.pack_start(dismiss_button, False, False, 0)

        back_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        back_button.get_style_context().add_class("notification-item-action")
        back_button.set_tooltip_text("Volver")
        back_button.add(
            Gtk.Image.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.MENU)
        )
        back_button.connect("clicked", self._on_tools_back_clicked)
        self._tools.pack_start(back_button, False, False, 0)

        content.pack_start(self._stack, True, True, 0)

    def _on_gear_clicked(self, _button: Gtk.Button) -> None:
        self._stack.set_visible_child_name("tools")

    def _on_tools_back_clicked(self, _button: Gtk.Button) -> None:
        self._stack.set_visible_child_name("face")

    def _tools_visible(self) -> bool:
        return self._stack.get_visible_child_name() == "tools"

    def _on_enter_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        mode = getattr(event, "mode", None)
        if mode in (Gdk.CrossingMode.GRAB, Gdk.CrossingMode.UNGRAB):
            return False
        # Moving among children still counts as being inside the card.
        if getattr(event, "detail", None) == Gdk.NotifyType.INFERIOR:
            return False
        if self._tools_visible():
            return False
        if len(self._group_snapshots) > 1:
            self._open_group_window()
            self._hover_opened = True
        return False

    def _on_leave_notify(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if not is_pointer_leaving_surface(event):
            return False
        if pointer_inside_widget(self):
            return False
        self._hover_opened = False
        if self._popover is not None and self._popover.get_visible():
            self._popover.hide()
        return False

    def _on_motion_notify(self, _widget: Gtk.Widget, _event: Gdk.EventMotion) -> bool:
        if self._tools_visible():
            return False
        if len(self._group_snapshots) > 1 and not self._hover_opened:
            self._open_group_window()
            self._hover_opened = True
        return False

    def _on_row_clicked(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False

        snapshot = self._representative
        if self._default_action is not None:
            self._on_invoke_action(snapshot.id, self._default_action)
        # Always focus-or-activate the sender; ActionInvoked alone is often a no-op.
        self._on_open_app(snapshot)
        return True

    def _open_group_window(self) -> None:
        if self._on_open_group_window is not None:
            self._on_open_group_window(self._group_snapshots, self, self.get_toplevel())


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
