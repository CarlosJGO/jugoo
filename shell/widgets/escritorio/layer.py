"""Full-monitor desktop icons surface (Layer BOTTOM + input regions)."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ...config import DESKTOP_ICON_CELL_HEIGHT, DESKTOP_ICON_CELL_WIDTH
from ...eventbus import EventBus
from ...identity import TITLE_DESKTOP
from ...servicios.escritorio.desktop_icons.opener import DesktopOpenError
from ...servicios.escritorio.desktop_icons.service import (
    DESKTOP_ICONS_CHANGED,
    DesktopIconsService,
)
from ...ui.disfraces.mask import apply_input_region, prepare_transparent_toplevel
from ...window_identity import configure_toplevel
from .icon_widget import DesktopIconWidget


class DesktopIconsLayer(Gtk.Window):
    """Transparent BOTTOM layer; only icon rectangles receive pointer input."""

    def __init__(
        self,
        application: Gtk.Application,
        event_bus: EventBus,
        service: DesktopIconsService,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_application(application)
        self._event_bus = event_bus
        self._service = service
        self._icons: dict[str, DesktopIconWidget] = {}
        self._enabled = True
        self._region_idle = 0

        prepare_transparent_toplevel(self)
        configure_toplevel(self, title=TITLE_DESKTOP)
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_accept_focus(False)
        self.set_can_focus(False)
        self.set_name("shell-desktop")
        self.get_style_context().add_class("desktop-layer")

        self._fixed = Gtk.Fixed()
        self._fixed.set_app_paintable(True)
        self.add(self._fixed)

        self._configure_layer_shell()
        self._event_bus.subscribe(DESKTOP_ICONS_CHANGED, self._on_icons_changed)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("realize", self._on_realize)
        self.connect("key-press-event", self._on_key_press)
        self.connect("destroy", self._on_destroy)
        self.connect("button-press-event", self._on_background_press)

        # Empty areas must not grab clicks: input shape starts empty until icons exist.
        GLib.idle_add(self._sync_from_service)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if self._enabled:
            self.show_all()
            self._schedule_input_region()
        else:
            self.hide()

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-desktop")
        # Below normal windows; wallpaper (swaybg/hyprpaper) typically sits on BACKGROUND.
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.BOTTOM)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
        monitor = _gdk_monitor()
        if monitor is not None:
            GtkLayerShell.set_monitor(self, monitor)
            geometry = monitor.get_geometry()
            self.set_size_request(geometry.width, geometry.height)

    def _sync_from_service(self) -> bool:
        self._render(
            self._service.shortcuts,
            selected_id=self._service.selected_id,
        )
        return False

    def _on_icons_changed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        shortcuts = payload.get("shortcuts") or ()
        selected_id = payload.get("selected_id")
        GLib.idle_add(self._render, shortcuts, selected_id)

    def _render(self, shortcuts, selected_id: object = None) -> bool:
        wanted = {item.id: item for item in shortcuts}
        # Remove stale widgets.
        for shortcut_id in tuple(self._icons):
            if shortcut_id not in wanted:
                widget = self._icons.pop(shortcut_id)
                self._fixed.remove(widget)
                widget.destroy()
        selected = selected_id if isinstance(selected_id, str) else None
        for shortcut in shortcuts:
            widget = self._icons.get(shortcut.id)
            if widget is None:
                widget = DesktopIconWidget(
                    shortcut,
                    on_activate=self._activate,
                    on_select=self._select,
                    on_move_end=self._move_end,
                    selected=shortcut.id == selected,
                )
                self._icons[shortcut.id] = widget
                self._fixed.put(widget, shortcut.x, shortcut.y)
            else:
                widget.set_selected(shortcut.id == selected)
                # Avoid fighting an in-progress drag: only reposition from store
                # when the widget is not being dragged (press_root is None).
                if (not widget.is_dragging) and widget.position != (shortcut.x, shortcut.y):
                    widget._current = (shortcut.x, shortcut.y)
                    self._fixed.move(widget, shortcut.x, shortcut.y)
        self._fixed.show_all()
        self._schedule_input_region()
        # Second pass after GTK allocates children (first pass can still see 0×0).
        GLib.timeout_add(50, self._refresh_input_region_later)
        return False

    def _refresh_input_region_later(self) -> bool:
        if self._region_idle:
            GLib.source_remove(self._region_idle)
            self._region_idle = 0
        self._refresh_input_region()
        return False

    def _activate(self, shortcut_id: str) -> None:
        try:
            self._service.open(shortcut_id)
        except DesktopOpenError as error:
            print(f"shell: desktop-icons: {error}")

    def _select(self, shortcut_id: str) -> None:
        self._service.select(shortcut_id)

    def _move_end(self, shortcut_id: str, x: int, y: int) -> None:
        width = max(1, self.get_allocated_width())
        height = max(1, self.get_allocated_height())
        clamped_x = max(0, min(x, width - DESKTOP_ICON_CELL_WIDTH))
        clamped_y = max(0, min(y, height - DESKTOP_ICON_CELL_HEIGHT))
        self._service.move(shortcut_id, clamped_x, clamped_y)
        self._schedule_input_region()

    def _on_background_press(self, _widget: Gtk.Widget, event) -> bool:
        # Clicks on empty desktop (if any reach us) clear selection.
        if event.button == 1:
            self._service.select(None)
        return False

    def _on_key_press(self, _widget: Gtk.Widget, event) -> bool:
        # Layer keyboard mode is NONE; kept for completeness if that changes.
        keyval = event.keyval
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            selected = self._service.selected_id
            if selected:
                self._service.remove(selected)
                return True
        return False

    def _on_size_allocate(self, _widget: Gtk.Widget, _allocation) -> None:
        self._schedule_input_region()

    def _on_realize(self, *_args) -> None:
        self._schedule_input_region()

    def _schedule_input_region(self) -> None:
        if self._region_idle:
            return
        self._region_idle = GLib.idle_add(self._refresh_input_region)

    def _refresh_input_region(self) -> bool:
        self._region_idle = 0
        if not self.get_realized():
            return False
        width = max(1, self.get_allocated_width())
        height = max(1, self.get_allocated_height())
        rects: list[tuple[int, int, int, int]] = []
        for widget in self._icons.values():
            # Prefer live allocation; fall back to stored position + cell size.
            # Empty/zero allocations used to yield an empty input region, so
            # icons were visible but clicks passed through to the wallpaper.
            alloc = widget.get_allocation()
            if alloc.width > 0 and alloc.height > 0:
                x, y, w, h = int(alloc.x), int(alloc.y), int(alloc.width), int(alloc.height)
            else:
                x, y = widget.position
                w, h = DESKTOP_ICON_CELL_WIDTH, DESKTOP_ICON_CELL_HEIGHT
            rects.append((x, y, w, h))
        region = _input_region_from_rects(width, height, rects)
        apply_input_region(self, region)
        return False

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(DESKTOP_ICONS_CHANGED, self._on_icons_changed)
        if self._region_idle:
            GLib.source_remove(self._region_idle)
            self._region_idle = 0


def _input_region_from_rects(
    width: int,
    height: int,
    rects: list[tuple[int, int, int, int]],
) -> cairo.Region:
    """Build a pointer-hit region covering only the given rectangles."""
    surface = cairo.ImageSurface(cairo.FORMAT_A8, width, height)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.set_source_rgba(1, 1, 1, 1)
    for x, y, w, h in rects:
        if w <= 0 or h <= 0:
            continue
        cr.rectangle(float(x), float(y), float(w), float(h))
        cr.fill()
    surface.flush()
    return Gdk.cairo_region_create_from_surface(surface)


def _gdk_monitor() -> Gdk.Monitor | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitor = display.get_primary_monitor()
    if monitor is None and display.get_n_monitors() > 0:
        monitor = display.get_monitor(0)
    return monitor
