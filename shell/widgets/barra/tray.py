"""System tray widget hosting StatusNotifierItem icons."""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GLib", "2.0")
gi.require_version("DbusmenuGtk3", "0.4")

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, DbusmenuGtk3

from ...config import (
    TRAY_COMPACT_ICON_SIZE,
    TRAY_COMPACT_SLOT_SIZE,
    TRAY_ICON_SIZE,
    TRAY_ITEM_SPACING,
    TRAY_SLOT_SIZE,
)
from ...servicios.bandeja.tray import SystemTrayService, TrayItemSnapshot
from ...ui import ShellModule

_MENU_POPUP_TIMEOUT_MS = 500


def _fit_pixbuf_to_icon_area(pixbuf: GdkPixbuf.Pixbuf, max_size: int) -> GdkPixbuf.Pixbuf:
    """Scale a tray pixbuf into a square bounding box without changing aspect ratio."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    if width <= 0 or height <= 0 or max_size <= 0:
        return pixbuf

    scale = min(max_size / width, max_size / height)
    target_w = max(1, int(round(width * scale)))
    target_h = max(1, int(round(height * scale)))
    if target_w == width and target_h == height:
        return pixbuf
    return pixbuf.scale_simple(
        target_w,
        target_h,
        GdkPixbuf.InterpType.BILINEAR,
    )


@dataclass
class _TrayItemView:
    event_box: Gtk.EventBox
    image: Gtk.Image
    menu: DbusmenuGtk3.Menu | None = None


class SystemTrayWidget(ShellModule):
    """Renders SNI tray icons; communication lives in SystemTrayService."""

    def __init__(self, tray_service: SystemTrayService) -> None:
        super().__init__("tray-widget", spacing=TRAY_ITEM_SPACING)

        self._service = tray_service
        self._items: dict[str, _TrayItemView] = {}
        self._compact = False

        self._container = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=TRAY_ITEM_SPACING,
        )
        self._container.get_style_context().add_class("tray-items")
        self.pack_start(self._container, False, False, 0)

        self._service.set_listener(self._on_items_changed)
        self._service.start()
        self.connect("destroy", self._on_destroy)

    def apply_shell_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        for item in self._service.snapshots:
            view = self._items.get(item.address)
            if view is None:
                continue
            self._apply_slot_geometry(view)
            self._update_item_view(view, item)
        self._queue_relayout()

    @property
    def _slot_size(self) -> int:
        return TRAY_COMPACT_SLOT_SIZE if self._compact else TRAY_SLOT_SIZE

    @property
    def _icon_size(self) -> int:
        return TRAY_COMPACT_ICON_SIZE if self._compact else TRAY_ICON_SIZE

    def _apply_slot_geometry(self, view: _TrayItemView) -> None:
        view.event_box.set_size_request(self._slot_size, self._slot_size)
        view.image.set_size_request(self._icon_size, self._icon_size)
        view.image.set_pixel_size(self._icon_size)

    def _queue_relayout(self) -> None:
        self.queue_resize()
        parent = self.get_parent()
        if parent is not None:
            parent.queue_resize()
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Widget):
            toplevel.queue_resize()

    def _on_destroy(self, *_args) -> None:
        self._service.set_listener(None)
        self._service.close()

    def _on_items_changed(self, items: tuple[TrayItemSnapshot, ...]) -> None:
        GLib.idle_add(self._render, items)

    def _render(self, items: tuple[TrayItemSnapshot, ...]) -> bool:
        had_items = bool(self._items)
        visible_ids = {item.address for item in items}

        for address, view in list(self._items.items()):
            if address not in visible_ids:
                self._destroy_item_view(view)
                self._container.remove(view.event_box)
                del self._items[address]

        for item in items:
            view = self._items.get(item.address)
            if view is None:
                view = self._create_item_view(item)
                self._items[item.address] = view
                self._container.pack_start(view.event_box, False, False, 0)
            self._update_item_view(view, item)

        if items:
            self.show()
            self.show_all()
            self._queue_relayout()
            GLib.idle_add(self._finalize_relayout)
        elif had_items:
            self.hide()

        self._container.show_all()
        return False

    def _finalize_relayout(self) -> bool:
        """Reapply slot geometry once GTK has mapped recreated tray items."""
        for view in self._items.values():
            self._apply_slot_geometry(view)
        self._queue_relayout()
        return False

    def _create_item_view(self, item: TrayItemSnapshot) -> _TrayItemView:
        event_box = Gtk.EventBox()
        event_box.set_visible_window(False)
        event_box.set_size_request(self._slot_size, self._slot_size)
        event_box.get_style_context().add_class("tray-item")
        event_box.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )

        image = Gtk.Image()
        image.set_pixel_size(self._icon_size)
        image.set_size_request(self._icon_size, self._icon_size)
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        event_box.add(image)

        event_box.connect("button-press-event", self._on_button_press, item.address)
        event_box.connect("scroll-event", self._on_scroll, item.address)
        event_box.connect("enter-notify-event", self._on_enter_notify)
        event_box.connect("leave-notify-event", self._on_leave_notify)

        view = _TrayItemView(event_box=event_box, image=image)
        self._ensure_menu(view, item)
        return view

    def _update_item_view(self, view: _TrayItemView, item: TrayItemSnapshot) -> None:
        if item.icon_pixbuf is not None:
            scaled = _fit_pixbuf_to_icon_area(item.icon_pixbuf, self._icon_size)
            view.image.set_from_pixbuf(scaled)
        elif item.icon_name:
            view.image.set_from_icon_name(item.icon_name, Gtk.IconSize.MENU)
            view.image.set_pixel_size(self._icon_size)
        else:
            view.image.set_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
            view.image.set_pixel_size(self._icon_size)

        if item.tooltip:
            view.event_box.set_tooltip_text(item.tooltip)
        else:
            view.event_box.set_tooltip_text(None)

        style = view.event_box.get_style_context()
        if item.status == "NeedsAttention":
            style.add_class("tray-item-attention")
        else:
            style.remove_class("tray-item-attention")

        self._ensure_menu(view, item)

    def _ensure_menu(self, view: _TrayItemView, item: TrayItemSnapshot) -> None:
        if not item.menu_bus or not item.menu_path:
            self._destroy_menu(view)
            return
        if view.menu is not None:
            return

        menu = DbusmenuGtk3.Menu.new(item.menu_bus, item.menu_path)
        menu.get_style_context().add_class("tray-menu")

        client = menu.get_client()
        accel_group = Gtk.AccelGroup()
        client.set_accel_group(accel_group)

        menu.attach_to_widget(view.event_box)
        view.menu = menu

    def _destroy_menu(self, view: _TrayItemView) -> None:
        if view.menu is None:
            return
        view.menu.popdown()
        view.menu.detach()
        view.menu = None

    def _destroy_item_view(self, view: _TrayItemView) -> None:
        self._destroy_menu(view)

    @staticmethod
    def _on_enter_notify(_widget: Gtk.EventBox, _event: Gdk.EventCrossing) -> bool:
        _widget.set_state_flags(Gtk.StateFlags.PRELIGHT)
        return False

    @staticmethod
    def _on_leave_notify(_widget: Gtk.EventBox, _event: Gdk.EventCrossing) -> bool:
        _widget.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        return False

    def _on_button_press(
        self,
        event_box: Gtk.EventBox,
        event: Gdk.EventButton,
        address: str,
    ) -> bool:
        item = self._find_item(address)
        if item is None:
            return False

        x, y = int(event.x_root), int(event.y_root)
        wants_menu = (event.button == 1 and item.item_is_menu) or event.button == 3

        if wants_menu:
            self._popup_menu(item, event_box, event)
            return True

        if event.button == 1:
            if self._service.activate(address, x, y):
                return True
            if item.menu_path:
                self._popup_menu(item, event_box, event)
                return True
            if item.supports_context_menu:
                self._service.context_menu(address, x, y)
            return True

        if event.button == 2 and item.supports_secondary_activate:
            self._service.secondary_activate(address, x, y)
            return True

        return False

    def _on_scroll(
        self,
        _event_box: Gtk.EventBox,
        event: Gdk.EventScroll,
        address: str,
    ) -> bool:
        if event.direction == Gdk.ScrollDirection.UP:
            delta, orientation = -1.0, 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta, orientation = 1.0, 1
        elif event.direction == Gdk.ScrollDirection.LEFT:
            delta, orientation = -1.0, 0
        elif event.direction == Gdk.ScrollDirection.RIGHT:
            delta, orientation = 1.0, 0
        else:
            return False

        item = self._find_item(address)
        if item is None or not item.supports_scroll:
            return False

        self._service.scroll(address, delta, orientation)
        return True

    def _popup_menu(
        self,
        item: TrayItemSnapshot,
        event_box: Gtk.EventBox,
        event: Gdk.EventButton,
    ) -> None:
        view = self._items.get(item.address)
        if view is None:
            return

        x, y = int(event.x_root), int(event.y_root)
        self._ensure_menu(view, item)
        menu = view.menu
        if menu is None:
            self._service.context_menu(item.address, x, y)
            return

        event_box.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        client = menu.get_client()

        def menu_has_items() -> bool:
            root = client.get_root()
            return root is not None and bool(root.get_children())

        handler_id = 0
        popup_done = False

        def show_menu() -> None:
            nonlocal popup_done, handler_id
            if popup_done:
                return
            popup_done = True
            if handler_id:
                client.disconnect(handler_id)
                handler_id = 0
            if menu_has_items():
                menu.popup_at_pointer(event)
            else:
                self._service.context_menu(item.address, x, y)

        if menu_has_items():
            show_menu()
            return

        def on_layout_updated(*_args) -> None:
            GLib.idle_add(lambda: (show_menu(), False)[1])

        handler_id = client.connect("layout-updated", on_layout_updated)

        def on_timeout() -> bool:
            show_menu()
            return False

        GLib.timeout_add(_MENU_POPUP_TIMEOUT_MS, on_timeout)

    def _find_item(self, address: str) -> TrayItemSnapshot | None:
        for item in self._service.snapshots:
            if item.address == address:
                return item
        return None
