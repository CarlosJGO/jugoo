"""Pinned-application dock mounted in the main bar surface."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import (
    PINNED_APP_COMPACT_ICON_SIZE,
    PINNED_APP_ICON_SIZE,
    PINNED_APP_SPACING,
    PINNED_APPS_VISIBLE_LIMIT,
    PINNED_OVERFLOW_OFFSET,
)
from ...eventbus import EventBus
from ...icons import FALLBACK_ICON
from ...models import (
    ActiveWindow,
    ApplicationsSnapshot,
    DesktopApplication,
    HyprlandSnapshot,
    snapshot_windows,
    split_pinned_dock,
    windows_for_application,
)
from ...popup_handle import PopupHandle, PopupOutsideDismiss, hide_popup, present_popup
from ...servicios.aplicaciones.applications import (
    APP_ACTIVATE_REQUESTED,
    APP_NEW_INSTANCE_REQUESTED,
    APP_PIN_REORDER_REQUESTED,
    APP_PIN_SEND_TO_OVERFLOW_REQUESTED,
    APP_PIN_TOGGLE_REQUESTED,
    APPLICATIONS_CHANGED,
)
from ...servicios.escritorio.hyprland import (
    ACTIVE_WINDOW_CHANGED,
    WINDOW_CLOSED,
    WINDOW_OPENED,
    WORKSPACE_CHANGED,
)
from ...ui import ShellModule
from ...widgets.aplicaciones.context_menu import popup_application_menu
from ...window_identity import (
    TITLE_PINNED_OVERFLOW,
    configure_passive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)

_PIN_DRAG_THRESHOLD_PX = 8
_PIN_DRAG_EVENT_MASK = (
    Gdk.EventMask.BUTTON_PRESS_MASK
    | Gdk.EventMask.BUTTON_RELEASE_MASK
    | Gdk.EventMask.BUTTON_MOTION_MASK
    | Gdk.EventMask.POINTER_MOTION_MASK
)


def _xy(value: object) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return int(value[1]), int(value[2])
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def _detach_widget(widget: Gtk.Widget) -> None:
    """Unparent a dock button, including the FlowBoxChild wrapper if present."""
    parent = widget.get_parent()
    if parent is None:
        return
    parent.remove(widget)
    if isinstance(parent, Gtk.FlowBoxChild):
        flowbox = parent.get_parent()
        if flowbox is not None:
            flowbox.remove(parent)


class PinnedAppButton(Gtk.Button):
    """One pinned application; CSS classes reflect Hyprland running/focused state."""

    def __init__(
        self,
        application: DesktopApplication,
        *,
        on_activate: Callable[[str], None],
        on_unpin: Callable[[str], None],
        on_new_instance: Callable[[str], None],
        on_send_to_extras: Callable[[str], None],
        icon_size: int,
    ) -> None:
        super().__init__()
        self.application_id = application.id
        self._on_unpin = on_unpin
        self._on_new_instance = on_new_instance
        self._on_send_to_extras = on_send_to_extras
        self._can_send_to_extras = False
        self._icon_size = icon_size
        self.get_style_context().add_class("pinned-app-button")
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_tooltip_text(application.name)

        self._image = Gtk.Image.new_from_icon_name(application.icon, Gtk.IconSize.MENU)
        self._image.set_pixel_size(icon_size)
        self.add(self._image)

        self.connect("clicked", lambda *_args: on_activate(application.id))
        self.connect("button-press-event", self._on_button_press)
        self.show_all()

    def configure(self, application: DesktopApplication, icon_size: int) -> None:
        self.application_id = application.id
        self._icon_size = icon_size
        self.set_tooltip_text(application.name)
        self._image.set_from_icon_name(application.icon, Gtk.IconSize.MENU)
        self._image.set_pixel_size(icon_size)

    def set_icon_size(self, icon_size: int) -> None:
        if self._icon_size == icon_size:
            return
        self._icon_size = icon_size
        self._image.set_pixel_size(icon_size)

    def update_runtime(self, *, running: bool, focused: bool) -> None:
        context = self.get_style_context()
        if running:
            context.add_class("running")
        else:
            context.remove_class("running")
        if focused:
            context.add_class("focused")
        else:
            context.remove_class("focused")

    def set_can_send_to_extras(self, enabled: bool) -> None:
        self._can_send_to_extras = enabled

    def _on_button_press(self, _button: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            return False
        entries: list[tuple[str, Callable[[], None]] | None] = [
            ("Nueva instancia", lambda: self._on_new_instance(self.application_id)),
        ]
        if self._can_send_to_extras:
            entries.append(
                ("Enviar a extras", lambda: self._on_send_to_extras(self.application_id)),
            )
        entries.extend(
            (
                None,
                ("Desfijar", lambda: self._on_unpin(self.application_id)),
            )
        )
        popup_application_menu(event, tuple(entries))
        return True


class PinnedAppsOverflowPopup(Gtk.Window):
    """Mini extension below the dock strip for pinned apps beyond the visible limit."""

    def __init__(self, shell_window: Gtk.Window) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._anchor: Gtk.Widget | None = None
        self._fixed_top: int | None = None

        self.set_name("shell-pinned-overflow")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_PINNED_OVERFLOW)
        configure_passive_popup(self)

        self._content = Gtk.FlowBox()
        self._content.get_style_context().add_class("pinned-apps-overflow-content")
        self._content.set_selection_mode(Gtk.SelectionMode.NONE)
        self._content.set_min_children_per_line(1)
        self._content.set_max_children_per_line(PINNED_APPS_VISIBLE_LIMIT)
        self._content.set_column_spacing(PINNED_APP_SPACING)
        self._content.set_row_spacing(PINNED_APP_SPACING)
        self._content.set_homogeneous(True)
        self.add(self._content)

    def open_for(self, anchor: Gtk.Widget) -> None:
        self._anchor = anchor
        self._fixed_top = None
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor = None
        self._fixed_top = None
        hide_popup(self)

    def host_buttons(self, buttons: tuple[Gtk.Widget, ...]) -> None:
        for child in list(self._content.get_children()):
            inner = child.get_child()
            if inner is not None:
                child.remove(inner)
            self._content.remove(child)
        for button in buttons:
            _detach_widget(button)
            self._content.insert(button, -1)
            button.show()
        self._content.show_all()
        if self.get_visible():
            schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._anchor is None:
            return False
        top = position_popup_below_anchor(
            self,
            self._anchor,
            title=TITLE_PINNED_OVERFLOW,
            offset=PINNED_OVERFLOW_OFFSET,
            fixed_top=self._fixed_top,
        )
        if self._fixed_top is None and top is not None:
            self._fixed_top = top
        return False


class PinnedAppsWidget(ShellModule):
    """Stable 9-slot strip; overflow opens a drop-down extension of the bar."""

    def __init__(self, event_bus: EventBus, shell_window: Gtk.Window) -> None:
        super().__init__("pinned-apps-widget", spacing=PINNED_APP_SPACING)
        self._event_bus = event_bus
        self._shell_window = shell_window
        self._compact = False
        self._snapshot = ApplicationsSnapshot()
        self._hyprland: HyprlandSnapshot | None = None
        self._active_address = ""
        self._buttons: dict[str, PinnedAppButton] = {}
        self._overflow_open = False
        self._dragged_app_id: str | None = None
        self._drag_target_id: str | None = None
        self._drag_press_root: tuple[float, float] | None = None
        self._pin_dragging = False
        self._suppress_activate = False

        self._primary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=PINNED_APP_SPACING)
        self._primary.get_style_context().add_class("pinned-apps-primary")
        self.pack_start(self._primary, False, False, 0)

        self._expand_button = Gtk.Button()
        self._expand_button.get_style_context().add_class("pinned-apps-expand")
        self._expand_button.set_relief(Gtk.ReliefStyle.NONE)
        self._expand_icon = Gtk.Image.new_from_icon_name(
            "view-app-grid-symbolic",
            Gtk.IconSize.DIALOG,
        )
        self._expand_icon.set_pixel_size(PINNED_APP_ICON_SIZE)
        self._expand_button.set_image(self._expand_icon)
        self._expand_button.set_always_show_image(True)
        self._expand_icon.show()
        self._expand_button.connect("clicked", self._on_toggle_expand)
        self.pack_start(self._expand_button, False, False, 0)

        self._overflow = PopupHandle(lambda: PinnedAppsOverflowPopup(shell_window))
        self._outside_click = PopupOutsideDismiss()

        self._expand_button.set_no_show_all(True)
        self._expand_button.hide()

        self._event_bus.subscribe(APPLICATIONS_CHANGED, self._on_applications_changed)
        self._event_bus.subscribe(WORKSPACE_CHANGED, self._on_hyprland_snapshot)
        self._event_bus.subscribe(WINDOW_OPENED, self._on_hyprland_snapshot)
        self._event_bus.subscribe(WINDOW_CLOSED, self._on_hyprland_snapshot)
        self._event_bus.subscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window)
        self.connect("destroy", self._on_destroy)
        self.set_no_show_all(True)
        self.hide()

    def apply_shell_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        size = PINNED_APP_COMPACT_ICON_SIZE if compact else PINNED_APP_ICON_SIZE
        self._expand_icon.set_pixel_size(size)
        for button in self._buttons.values():
            button.set_icon_size(size)

    def _icon_size(self) -> int:
        return PINNED_APP_COMPACT_ICON_SIZE if self._compact else PINNED_APP_ICON_SIZE

    def _on_applications_changed(self, snapshot: ApplicationsSnapshot) -> None:
        if not isinstance(snapshot, ApplicationsSnapshot):
            return
        GLib.idle_add(self._apply_snapshot, snapshot)

    def _on_hyprland_snapshot(self, snapshot: HyprlandSnapshot) -> None:
        if not isinstance(snapshot, HyprlandSnapshot):
            return
        self._hyprland = snapshot
        self._active_address = snapshot.active_window.address
        GLib.idle_add(self._apply_runtime)

    def _on_active_window(self, active_window: ActiveWindow) -> None:
        if not isinstance(active_window, ActiveWindow):
            return
        self._active_address = active_window.address
        GLib.idle_add(self._apply_runtime)

    def _apply_snapshot(self, snapshot: ApplicationsSnapshot) -> bool:
        previous_ids = self._snapshot.pinned_ids
        self._snapshot = snapshot
        if previous_ids != snapshot.pinned_ids:
            self._render_pinned()
        self._apply_runtime()
        return False

    def _render_pinned(self) -> None:
        visible_ids, overflow_ids, has_overflow = split_pinned_dock(
            self._snapshot.pinned_ids,
            PINNED_APPS_VISIBLE_LIMIT,
        )
        live_ids = set(visible_ids) | set(overflow_ids)
        for app_id in tuple(self._buttons):
            if app_id not in live_ids:
                button = self._buttons.pop(app_id)
                _detach_widget(button)

        self._fill_row(self._primary, visible_ids)
        overflow_popup = self._overflow.maybe
        if overflow_popup is not None:
            overflow_popup.host_buttons(self._overflow_buttons(overflow_ids))

        if self._snapshot.pinned_ids:
            self.set_no_show_all(False)
            self.show_all()
            if has_overflow:
                self._expand_button.show()
                self._expand_icon.show()
            else:
                self._close_overflow()
                self._expand_button.hide()
        else:
            self._close_overflow()
            self.hide()
        self._sync_send_to_extras(visible_ids, has_overflow)
        self._sync_expand_button()

    def _fill_row(self, row: Gtk.Box, app_ids: tuple[str, ...]) -> None:
        for child in list(row.get_children()):
            row.remove(child)
        for app_id in app_ids:
            button = self._button_for(self._application_for(app_id))
            _detach_widget(button)
            row.pack_start(button, False, False, 0)
            button.show()

    def _overflow_buttons(self, app_ids: tuple[str, ...]) -> tuple[Gtk.Widget, ...]:
        return tuple(self._button_for(self._application_for(app_id)) for app_id in app_ids)

    def _application_for(self, app_id: str) -> DesktopApplication:
        return self._snapshot.app_by_id(app_id) or DesktopApplication(
            id=app_id,
            name=app_id,
            icon=FALLBACK_ICON,
        )

    def _button_for(self, application: DesktopApplication) -> PinnedAppButton:
        button = self._buttons.get(application.id)
        if button is None:
            button = PinnedAppButton(
                application,
                on_activate=self._on_activate,
                on_unpin=self._on_unpin,
                on_new_instance=self._on_new_instance,
                on_send_to_extras=self._on_send_to_extras,
                icon_size=self._icon_size(),
            )
            self._buttons[application.id] = button
            self._configure_pin_drag(button)
        else:
            button.configure(application, self._icon_size())
        return button

    def _apply_runtime(self) -> bool:
        windows = snapshot_windows(self._hyprland)
        for app_id in self._snapshot.pinned_ids:
            button = self._buttons.get(app_id)
            if button is None:
                continue
            application = self._application_for(app_id)
            matches = windows_for_application(application, windows)
            focused = any(window.address == self._active_address for window in matches)
            button.update_runtime(running=bool(matches), focused=focused)
        return False

    def _on_activate(self, app_id: str) -> None:
        if self._suppress_activate:
            return
        self._event_bus.emit(APP_ACTIVATE_REQUESTED, app_id)

    def _on_unpin(self, app_id: str) -> None:
        self._event_bus.emit(APP_PIN_TOGGLE_REQUESTED, app_id)

    def _on_new_instance(self, app_id: str) -> None:
        self._event_bus.emit(APP_NEW_INSTANCE_REQUESTED, app_id)

    def _on_send_to_extras(self, app_id: str) -> None:
        self._event_bus.emit(APP_PIN_SEND_TO_OVERFLOW_REQUESTED, app_id)

    def _sync_send_to_extras(self, visible_ids: tuple[str, ...], has_overflow: bool) -> None:
        visible = set(visible_ids)
        for app_id, button in self._buttons.items():
            button.set_can_send_to_extras(has_overflow and app_id in visible)

    def _on_toggle_expand(self, *_args) -> None:
        _, overflow_ids, has_overflow = split_pinned_dock(
            self._snapshot.pinned_ids,
            PINNED_APPS_VISIBLE_LIMIT,
        )
        if not has_overflow:
            self._close_overflow()
            return
        if self._overflow_open:
            self._close_overflow()
            return
        popup = self._overflow.get()
        popup.host_buttons(self._overflow_buttons(overflow_ids))
        popup.open_for(self._expand_button)
        self._overflow_open = True
        self._outside_click.install(
            popup,
            self._shell_window,
            (self._expand_button,),
            self._close_overflow,
            self._event_bus,
        )
        self._sync_expand_button()

    def _close_overflow(self) -> None:
        self._overflow_open = False
        self._outside_click.uninstall()
        popup = self._overflow.maybe
        if popup is not None:
            popup.close_popup()
        self._sync_expand_button()

    def _sync_expand_button(self) -> None:
        expanded = self._overflow_open
        self._expand_icon.set_pixel_size(self._icon_size())
        self._expand_icon.show()
        context = self._expand_button.get_style_context()
        if expanded:
            context.add_class("expanded")
        else:
            context.remove_class("expanded")

    def _configure_pin_drag(self, button: PinnedAppButton) -> None:
        if getattr(button, "_pin_drag_configured", False):
            return
        button._pin_drag_configured = True
        button.add_events(_PIN_DRAG_EVENT_MASK)
        button.connect("button-press-event", self._on_pin_drag_press)
        button.connect("motion-notify-event", self._on_pin_drag_motion)
        button.connect("button-release-event", self._on_pin_drag_release)

    def _on_pin_drag_press(self, button: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        app_id = getattr(button, "application_id", "")
        if not isinstance(app_id, str) or not app_id:
            return False
        self._dragged_app_id = app_id
        self._drag_target_id = None
        self._drag_press_root = (float(event.x_root), float(event.y_root))
        self._pin_dragging = False
        return False

    def _on_pin_drag_motion(self, widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._dragged_app_id is None or self._drag_press_root is None:
            return False
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        if not self._pin_dragging:
            delta_x = float(event.x_root) - self._drag_press_root[0]
            delta_y = float(event.y_root) - self._drag_press_root[1]
            if (delta_x * delta_x) + (delta_y * delta_y) < _PIN_DRAG_THRESHOLD_PX ** 2:
                return False
            self._pin_dragging = True
            self._suppress_activate = True
            source_button = self._buttons.get(self._dragged_app_id)
            if source_button is not None:
                source_button.get_style_context().add_class("dragging")
        target_id = self._pin_id_at_widget_point(widget, event.x, event.y)
        self._drag_target_id = target_id
        self._update_drop_target_style(target_id)
        return True

    def _on_pin_drag_release(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        source_id = self._dragged_app_id
        was_dragging = self._pin_dragging
        target_id = self._pin_id_at_widget_point(widget, event.x, event.y)
        if target_id is None:
            target_id = self._drag_target_id
        self._reset_pin_drag()
        if was_dragging and source_id and target_id and target_id != source_id:
            self._event_bus.emit(
                APP_PIN_REORDER_REQUESTED,
                {"source_id": source_id, "target_id": target_id},
            )
            GLib.timeout_add(100, self._clear_suppress_activate)
            return True
        if was_dragging:
            GLib.timeout_add(100, self._clear_suppress_activate)
            return True
        self._suppress_activate = False
        return False

    def _pin_id_at_widget_point(self, widget: Gtk.Widget, x: float, y: float) -> str | None:
        pointer = _xy(widget.translate_coordinates(self, int(x), int(y)))
        if pointer is None:
            return None
        pointer_x, pointer_y = pointer
        source_id = self._dragged_app_id
        for app_id, button in self._buttons.items():
            if app_id == source_id or not button.get_mapped():
                continue
            if button.get_toplevel() is not self.get_toplevel():
                continue
            origin = _xy(button.translate_coordinates(self, 0, 0))
            if origin is None:
                continue
            button_x, button_y = origin
            allocation = button.get_allocation()
            if (
                button_x <= pointer_x < button_x + allocation.width
                and button_y <= pointer_y < button_y + allocation.height
            ):
                return app_id
        return None

    def _update_drop_target_style(self, target_id: str | None) -> None:
        source_id = self._dragged_app_id
        for app_id, button in self._buttons.items():
            style = button.get_style_context()
            if target_id is not None and app_id == target_id and app_id != source_id:
                style.add_class("drop-target")
            else:
                style.remove_class("drop-target")

    def _reset_pin_drag(self) -> None:
        self._clear_drag_styles()
        self._dragged_app_id = None
        self._drag_target_id = None
        self._drag_press_root = None
        self._pin_dragging = False

    def _clear_suppress_activate(self) -> bool:
        self._suppress_activate = False
        return False

    def _clear_drag_styles(self) -> None:
        for button in self._buttons.values():
            button.get_style_context().remove_class("dragging")
            button.get_style_context().remove_class("drop-target")

    def _on_destroy(self, *_args) -> None:
        self._reset_pin_drag()
        self._close_overflow()
        self._event_bus.unsubscribe(APPLICATIONS_CHANGED, self._on_applications_changed)
        self._event_bus.unsubscribe(WORKSPACE_CHANGED, self._on_hyprland_snapshot)
        self._event_bus.unsubscribe(WINDOW_OPENED, self._on_hyprland_snapshot)
        self._event_bus.unsubscribe(WINDOW_CLOSED, self._on_hyprland_snapshot)
        self._event_bus.unsubscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window)
