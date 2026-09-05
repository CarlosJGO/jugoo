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
    PINNED_DOCK_REVEAL_MS,
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
from ...servicios.aplicaciones.applications import (
    APP_ACTIVATE_REQUESTED,
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


class PinnedAppButton(Gtk.Button):
    """One pinned application; CSS classes reflect Hyprland running/focused state."""

    def __init__(
        self,
        application: DesktopApplication,
        *,
        on_activate: Callable[[str], None],
        on_unpin: Callable[[str], None],
        icon_size: int,
    ) -> None:
        super().__init__()
        self.application_id = application.id
        self._on_unpin = on_unpin
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
        if icon_size == self._icon_size:
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

    def _on_button_press(self, _button: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Desfijar")
        item.connect("activate", lambda *_args: self._on_unpin(self.application_id))
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True


class PinnedAppsWidget(ShellModule):
    """Stable 9-slot strip plus an in-bar Gtk.Revealer for overflow apps."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__("pinned-apps-widget", spacing=PINNED_APP_SPACING)
        self._event_bus = event_bus
        self._compact = False
        self._snapshot = ApplicationsSnapshot()
        self._hyprland: HyprlandSnapshot | None = None
        self._active_address = ""
        self._buttons: dict[str, PinnedAppButton] = {}
        self._expanded = False

        self._primary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=PINNED_APP_SPACING)
        self._primary.get_style_context().add_class("pinned-apps-primary")
        self.pack_start(self._primary, False, False, 0)

        self._expand_button = Gtk.Button()
        self._expand_button.get_style_context().add_class("pinned-apps-expand")
        self._expand_button.set_relief(Gtk.ReliefStyle.NONE)
        self._expand_button.set_tooltip_text("Mostrar más aplicaciones")
        self._expand_icon = Gtk.Image.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.MENU)
        self._expand_icon.set_pixel_size(PINNED_APP_ICON_SIZE)
        self._expand_button.add(self._expand_icon)
        self._expand_button.connect("clicked", self._on_toggle_expand)
        self.pack_start(self._expand_button, False, False, 0)

        self._overflow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=PINNED_APP_SPACING)
        self._overflow.get_style_context().add_class("pinned-apps-overflow")
        self._revealer = Gtk.Revealer()
        self._revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self._revealer.set_transition_duration(PINNED_DOCK_REVEAL_MS)
        self._revealer.set_reveal_child(False)
        self._revealer.add(self._overflow)
        self.pack_start(self._revealer, False, False, 0)

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
                parent = button.get_parent()
                if parent is not None:
                    parent.remove(button)

        self._fill_row(self._primary, visible_ids)
        self._fill_row(self._overflow, overflow_ids)

        if self._snapshot.pinned_ids:
            self.set_no_show_all(False)
            self.show_all()
            self._revealer.set_reveal_child(self._expanded)
            if has_overflow:
                self._expand_button.show()
            else:
                self._expanded = False
                self._revealer.set_reveal_child(False)
                self._expand_button.hide()
        else:
            self._expanded = False
            self._revealer.set_reveal_child(False)
            self.hide()
        self._sync_expand_button()

    def _fill_row(self, row: Gtk.Box, app_ids: tuple[str, ...]) -> None:
        for child in list(row.get_children()):
            row.remove(child)
        for app_id in app_ids:
            application = self._snapshot.app_by_id(app_id)
            if application is None:
                application = DesktopApplication(id=app_id, name=app_id, icon=FALLBACK_ICON)
            button = self._button_for(application)
            row.pack_start(button, False, False, 0)
            button.show()

    def _button_for(self, application: DesktopApplication) -> PinnedAppButton:
        button = self._buttons.get(application.id)
        if button is None:
            button = PinnedAppButton(
                application,
                on_activate=self._on_activate,
                on_unpin=self._on_unpin,
                icon_size=self._icon_size(),
            )
            self._buttons[application.id] = button
        else:
            button.configure(application, self._icon_size())
        return button

    def _apply_runtime(self) -> bool:
        windows = snapshot_windows(self._hyprland)
        for app_id in self._snapshot.pinned_ids:
            button = self._buttons.get(app_id)
            if button is None:
                continue
            application = self._snapshot.app_by_id(app_id) or DesktopApplication(
                id=app_id,
                name=app_id,
                icon=FALLBACK_ICON,
            )
            matches = windows_for_application(application, windows)
            focused = any(window.address == self._active_address for window in matches)
            button.update_runtime(running=bool(matches), focused=focused)
        return False

    def _on_activate(self, app_id: str) -> None:
        self._event_bus.emit(APP_ACTIVATE_REQUESTED, app_id)

    def _on_unpin(self, app_id: str) -> None:
        self._event_bus.emit(APP_PIN_TOGGLE_REQUESTED, app_id)

    def _on_toggle_expand(self, *_args) -> None:
        _, overflow_ids, has_overflow = split_pinned_dock(
            self._snapshot.pinned_ids,
            PINNED_APPS_VISIBLE_LIMIT,
        )
        if not has_overflow:
            return
        self._expanded = not self._expanded
        if self._expanded and overflow_ids:
            self._overflow.show_all()
        self._revealer.set_reveal_child(self._expanded)
        self._sync_expand_button()

    def _sync_expand_button(self) -> None:
        icon_name = "pan-start-symbolic" if self._expanded else "pan-end-symbolic"
        self._expand_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)
        self._expand_icon.set_pixel_size(self._icon_size())
        self._expand_button.set_tooltip_text(
            "Ocultar aplicaciones extra" if self._expanded else "Mostrar más aplicaciones"
        )
        context = self._expand_button.get_style_context()
        if self._expanded:
            context.add_class("expanded")
        else:
            context.remove_class("expanded")

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(APPLICATIONS_CHANGED, self._on_applications_changed)
        self._event_bus.unsubscribe(WORKSPACE_CHANGED, self._on_hyprland_snapshot)
        self._event_bus.unsubscribe(WINDOW_OPENED, self._on_hyprland_snapshot)
        self._event_bus.unsubscribe(WINDOW_CLOSED, self._on_hyprland_snapshot)
        self._event_bus.unsubscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window)
