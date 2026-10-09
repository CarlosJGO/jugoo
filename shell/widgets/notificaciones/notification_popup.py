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
    NOTIFICATION_POPUP_WIDTH,
    POPUP_EDGE_MARGIN,
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
from .notification_dismiss_drag import connect_right_drag_dismiss
from ...ui.theme import active_theme
from ...window_identity import (
    TITLE_NOTIFICATIONS,
    compute_popup_top_left,
    configure_interactive_popup,
    configure_toplevel,
    popup_window_size,
    register_shell_popup,
    schedule_popup_position,
    shell_window_bottom,
    monitor_containing_point,
    anchor_button_geometry,
    reposition_popup,
    reposition_popup_live,
)
from ...popup_spawn import publish_popup_spawn
from .notification_grouping import group_notification_snapshots
from .notification_mini_row import NotificationMiniRow

_URGENCY_LABELS = {
    0: "Baja",
    1: "Normal",
    2: "Urgente",
}
# One frame so the mapped chrome paints before the first block is built.
_FIRST_BLOCK_DELAY_MS = 32
# Grace so the pointer can move from a row into the stack pane.
_STACK_HIDE_MS = 80


def stack_pane_left(closed_right: int, width: int) -> int:
    """Keep the list's right edge fixed while the window grows to the left."""
    return int(closed_right) - int(width)


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
        self._dismiss_drag = connect_right_drag_dismiss(self, snapshot.id, on_dismiss)
        self.set_tooltip_text("Arrastra a izquierda o derecha con clic derecho para eliminar")
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
        is_sound_enabled: Callable[[], bool],
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
        self._is_sound_enabled = is_sound_enabled
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
        self._pinned_size: tuple[int, int] = (0, 0)
        self._pin_dirty = False
        self._pin_source_id = 0
        self._closed_right: int | None = None
        self._stack_open = False
        self._stacked_ids: tuple[int, ...] = ()
        self._stack_hide_id = 0
        self.connect("size-allocate", self._on_size_allocate_pin_top)

        self.set_name("shell-notifications")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATIONS)
        configure_interactive_popup(self)
        self.set_default_size(NOTIFICATION_POPUP_WIDTH, -1)
        self.set_size_request(NOTIFICATION_POPUP_WIDTH, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer.get_style_context().add_class("notification-popup-content")
        outer.get_style_context().add_class("notification-popup-split")
        dress_window(self, WindowRole.NOTIFICATIONS_POPUP, outer)

        self._stack_revealer = Gtk.Revealer()
        self._stack_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self._stack_revealer.set_transition_duration(NOTIFICATION_POPUP_ROW_APPEAR_MS)
        self._stack_revealer.set_reveal_child(False)
        self._stack_revealer.set_vexpand(True)
        self._stack_revealer.set_hexpand(False)
        outer.pack_start(self._stack_revealer, False, False, 0)

        self._stack_event = Gtk.EventBox()
        self._stack_event.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._stack_event.connect("enter-notify-event", self._on_stack_enter)
        self._stack_event.connect("leave-notify-event", self._on_stack_leave)
        self._stack_revealer.add(self._stack_event)

        stack_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        stack_pane.get_style_context().add_class("notification-stack-pane")
        stack_pane.set_size_request(NOTIFICATION_POPUP_WIDTH, -1)
        stack_pane.set_vexpand(True)
        self._stack_pane = stack_pane
        self._stack_event.add(stack_pane)

        self._stack_title = Gtk.Label(xalign=0)
        self._stack_title.get_style_context().add_class("notification-stack-title")
        self._stack_title.set_ellipsize(Pango.EllipsizeMode.END)
        stack_pane.pack_start(self._stack_title, False, False, 0)

        self._stack_scrolled = Gtk.ScrolledWindow()
        self._stack_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Height follows the parent list. Extra stacked rows scroll inside it.
        self._stack_scrolled.set_propagate_natural_height(False)
        self._stack_scrolled.set_vexpand(True)
        self._stack_scrolled.get_style_context().add_class("notification-popup-scroll")
        stack_pane.pack_start(self._stack_scrolled, True, True, 0)

        self._stack_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=NOTIFICATION_POPUP_LIST_SPACING,
        )
        self._stack_list.get_style_context().add_class("notification-group-window-list")
        self._stack_scrolled.add(self._stack_list)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("notification-popup-main")
        # Fixed geometry: the parent list never grows, shrinks, or reflows
        # when the stack pane opens or closes.
        main.set_size_request(NOTIFICATION_POPUP_WIDTH, -1)
        main.set_hexpand(False)
        main.set_vexpand(False)
        main.set_halign(Gtk.Align.END)
        main.set_valign(Gtk.Align.START)
        self._main = main
        main.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        main.connect("motion-notify-event", self._on_main_motion)
        outer.pack_end(main, False, False, 0)

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

        self._sound_disabled_banner = Gtk.Label(
            label="Sonido de notificaciones desactivado en Ajustes",
            xalign=0,
        )
        self._sound_disabled_banner.get_style_context().add_class(
            "notification-popup-sound-disabled"
        )
        self._sound_disabled_banner.set_no_show_all(True)
        header.pack_start(self._sound_disabled_banner, False, False, 0)
        self.refresh_sound_status()

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
        main.pack_start(header, False, False, 0)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_propagate_natural_height(True)
        self._scrolled.set_max_content_height(NOTIFICATION_POPUP_MAX_HEIGHT)
        self._scrolled.get_style_context().add_class("notification-popup-scroll")
        main.pack_start(self._scrolled, False, False, 0)

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

    def refresh_sound_status(self) -> None:
        if self._is_sound_enabled():
            self._sound_disabled_banner.hide()
        else:
            self._sound_disabled_banner.show()

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._cancel_progressive_reveal()
        self._cancel_top_pin()
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._pinned_size = (0, 0)
        self._closed_right = None
        self._position = None
        self.hide_stacked()
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
        # The window is already on screen. Blocks are built later, one per tick.
        self._arm_progressive_reveal()

    def close_popup(self) -> None:
        self._cancel_progressive_reveal()
        self._cancel_top_pin()
        self._anchor_button = None
        self._fixed_popup_top = None
        self._pinned_size = (0, 0)
        self._closed_right = None
        self._pin_dirty = False
        self.hide_stacked()
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    @property
    def position(self) -> tuple[int, int, int] | None:
        return self._position

    def refresh(self) -> None:
        """Rebuild the list. An in-progress open keeps stacking one block at a time."""
        self.hide_stacked()
        if self.get_visible() and self._anchor_button is not None and (
            self._reveal_source_id or self._pending_groups
        ):
            self._prepare_open_shell()
            self._arm_progressive_reveal()
            return
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

    def _arm_progressive_reveal(self) -> None:
        self._cancel_progressive_reveal()
        self._reveal_source_id = GLib.timeout_add(
            _FIRST_BLOCK_DELAY_MS,
            self._start_progressive_reveal,
        )

    def _cancel_progressive_reveal(self) -> None:
        if self._reveal_source_id:
            GLib.source_remove(self._reveal_source_id)
            self._reveal_source_id = 0
        self._pending_groups = []
        self._reveal_index = 0

    def _start_progressive_reveal(self) -> bool:
        self._reveal_source_id = 0
        if self._anchor_button is None:
            return False
        snapshots = self._sorted_history()
        if not snapshots:
            self._show_empty_state()
            if self._on_preload_group_pages is not None:
                self._on_preload_group_pages([])
            self._scrolled.queue_resize()
            return False

        if self._empty_label.get_parent() is not None:
            self._list_box.remove(self._empty_label)
        self._empty_label.hide()
        self._pending_groups = list(group_notification_snapshots(snapshots))
        self._reveal_index = 0
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
        self._pack_growing_row(self._build_group_row(group))

        if self._reveal_index >= len(self._pending_groups):
            self._reveal_source_id = 0
            self._finish_progressive_reveal()
            return False
        return True

    def _pack_growing_row(self, row: Gtk.Widget) -> None:
        """Slide one block in so the window lengthens with it, up to the scrolled max."""
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_transition_duration(self._row_grow_ms())
        revealer.set_reveal_child(False)
        revealer.add(row)
        self._list_box.pack_start(revealer, False, False, 0)
        revealer.show_all()
        revealer.set_reveal_child(True)

    def _row_grow_ms(self) -> int:
        theme = active_theme()
        if theme is not None and not theme.animation.enabled:
            return 0
        duration = NOTIFICATION_POPUP_ROW_APPEAR_MS
        if theme is not None and theme.animation.duration > 0:
            return min(duration, theme.animation.duration)
        return duration

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
            self._pin_top_to_bar()

    def _on_size_allocate_pin_top(self, _window: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        """Hyprland grows floating windows from their center, so each taller
        frame would slide the panel up over the bar. Re-pin the top every time.
        """
        size = (int(allocation.width), int(allocation.height))
        if self._fixed_popup_top is None or self._anchor_button is None:
            return
        if size[0] <= 1 or size[1] <= 1 or size == self._pinned_size:
            return
        self._pinned_size = size
        # One correction per size. Extra passes restart Hyprland's move and
        # the parent list appears to bounce.
        self._pin_top_to_bar()

    def _pin_top_to_bar(self) -> None:
        self._position_after_show(live=True)

    def _cancel_top_pin(self) -> None:
        self._pin_dirty = False
        if self._pin_source_id:
            GLib.source_remove(self._pin_source_id)
            self._pin_source_id = 0

    def show_stacked(self, group: list[NotificationSnapshot]) -> None:
        """Open the left pane on this group's stacked notifications."""
        if len(group) < 2:
            self.hide_stacked()
            return
        self._cancel_stack_hide()
        ids = tuple(snapshot.id for snapshot in group)
        if ids != self._stacked_ids:
            self._fill_stack(group)
            self._stacked_ids = ids
        self._stack_open = True
        self._stack_revealer.set_transition_duration(self._row_grow_ms())
        self._stack_revealer.set_reveal_child(True)

    def hide_stacked(self) -> None:
        if not self._stack_open and (
            getattr(self, "_stack_revealer", None) is None
            or not self._stack_revealer.get_reveal_child()
        ):
            return
        self._cancel_stack_hide()
        self._stack_open = False
        self._stacked_ids = ()
        self._stack_revealer.set_transition_duration(self._row_grow_ms())
        self._stack_revealer.set_reveal_child(False)

    def schedule_hide_stacked(self) -> None:
        if not self._stack_open or self._stack_hide_id:
            return
        self._stack_hide_id = GLib.timeout_add(_STACK_HIDE_MS, self._hide_stacked_if_pointer_left)

    def _hide_stacked_if_pointer_left(self) -> bool:
        self._stack_hide_id = 0
        if pointer_inside_widget(self._stack_event) or self._pointer_on_grouped_row():
            return False
        self.hide_stacked()
        return False

    def _cancel_stack_hide(self) -> None:
        if self._stack_hide_id:
            GLib.source_remove(self._stack_hide_id)
            self._stack_hide_id = 0

    def _fill_stack(self, group: list[NotificationSnapshot]) -> None:
        for child in list(self._stack_list.get_children()):
            self._stack_list.remove(child)
        head = group[0]
        label = head.summary or head.app_name
        self._stack_title.set_text(f"{label}  ·  {len(group)}")
        for snapshot in group:
            self._stack_list.pack_start(
                NotificationMiniRow(snapshot, on_dismiss=self._on_dismiss),
                False,
                False,
                0,
            )
        self._stack_list.show_all()

    def _pointer_on_grouped_row(self) -> bool:
        for child in self._list_box.get_children():
            row = child.get_child() if isinstance(child, Gtk.Revealer) else child
            if (
                isinstance(row, NotificationGroupRow)
                and len(row._group_snapshots) > 1
                and pointer_inside_widget(row)
            ):
                return True
        return False

    def _on_main_motion(self, _widget: Gtk.Widget, _event: Gdk.EventMotion) -> bool:
        if not self._stack_open or self._pointer_on_grouped_row():
            return False
        self.schedule_hide_stacked()
        return False

    def _on_stack_enter(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if getattr(event, "detail", None) == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_stack_hide()
        return False

    def _on_stack_leave(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if not is_pointer_leaving_surface(event):
            return False
        self.schedule_hide_stacked()
        return False

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

    def _position_after_show(self, *, live: bool = False) -> bool:
        if self._anchor_button is None:
            return False
        geometry = anchor_button_geometry(self._anchor_button)
        if geometry is None:
            return False
        _alloc_width, height = popup_window_size(self)
        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        closed_left, top = compute_popup_top_left(
            button_center_x=geometry.center_x,
            button_bottom=geometry.bottom,
            popup_width=NOTIFICATION_POPUP_WIDTH,
            popup_height=height,
            offset=NOTIFICATION_POPUP_OFFSET,
            fixed_top=self._fixed_popup_top,
            monitor=monitor,
            floor_top=shell_window_bottom(self._anchor_button),
        )
        if self._closed_right is None:
            self._closed_right = closed_left + NOTIFICATION_POPUP_WIDTH
        width = _alloc_width if _alloc_width > 1 else NOTIFICATION_POPUP_WIDTH
        left = stack_pane_left(self._closed_right, width)
        if monitor is not None:
            left = max(monitor.x + POPUP_EDGE_MARGIN, left)
        self._position = (left, top, width)
        if live:
            reposition_popup_live(self, title=TITLE_NOTIFICATIONS, x=left, y=top)
        else:
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
        self._dismiss_drag = connect_right_drag_dismiss(
            self,
            self._representative.id,
            lambda _notification_id: self._on_dismiss_group(self._group_snapshots),
        )
        subject = "grupo de notificaciones" if len(group_snapshots) > 1 else "notificación"
        self.set_tooltip_text(
            f"Arrastra a izquierda o derecha con clic derecho para eliminar {subject}"
        )

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
        host = self._host_popup()
        if len(self._group_snapshots) <= 1:
            if host is not None:
                host.hide_stacked()
            return False
        # Moving among children of this same grouped card.
        if getattr(event, "detail", None) == Gdk.NotifyType.INFERIOR:
            return False
        if self._tools_visible():
            return False
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
        host = self._host_popup()
        if host is not None:
            host.schedule_hide_stacked()
        return False

    def pointer_wants_stack(self) -> bool:
        return self._hover_opened and len(self._group_snapshots) > 1

    def _host_popup(self) -> NotificationPopup | None:
        toplevel = self.get_toplevel()
        if isinstance(toplevel, NotificationPopup):
            return toplevel
        return None

    def _on_motion_notify(self, _widget: Gtk.Widget, _event: Gdk.EventMotion) -> bool:
        if self._tools_visible():
            return False
        if len(self._group_snapshots) <= 1:
            host = self._host_popup()
            if host is not None:
                host.hide_stacked()
            return False
        if not self._hover_opened:
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
