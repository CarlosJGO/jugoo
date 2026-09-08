"""Host that owns silhouette geometry, rocky/meteor paint, and GTK content."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .base import Disguise, add_disguise_classes
from .mask import apply_input_region, clear_to_transparent, input_region_from_shape


class ShapedDisguiseHost(Gtk.EventBox):
    """Rectangular GDK allocation; visible/input silhouette comes from the disguise."""

    def __init__(self, disguise: Disguise, content: Gtk.Widget, *, animate: bool = False) -> None:
        super().__init__()
        self._disguise = disguise
        self._content = content
        self._animate = animate
        self._elapsed_ms = 0
        self._tick_id = 0
        self._region_key = (0, 0)

        self.set_app_paintable(True)
        self.set_above_child(False)
        self.set_visible_window(True)
        self.set_hexpand(True)
        self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
        add_disguise_classes(
            self,
            disguise.id,
            "disguise-shaped-host",
            f"disguise-{disguise.id.value}-host",
            *disguise.style_classes(),
        )

        for name in disguise.style_classes():
            content.get_style_context().add_class(name)
        content.get_style_context().add_class("disguise-shaped-content")
        content.get_style_context().add_class(f"disguise-{disguise.id.value}-content")

        self.add(content)
        self._apply_content_insets()

        self.connect("draw", self._on_draw)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("realize", self._on_realize)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)

    @property
    def disguise(self) -> Disguise:
        return self._disguise

    def _apply_content_insets(self) -> None:
        width = max(1, self.get_allocated_width())
        height = max(1, self.get_allocated_height())
        # Before first allocate, use a generous default based on requested size.
        req = self._content.get_preferred_size()[1]
        if width <= 1:
            width = max(req.width, 320)
        if height <= 1:
            height = max(req.height, 200)
        insets = self._disguise.content_insets(float(width), float(height))
        self._content.set_margin_top(insets.top)
        self._content.set_margin_end(insets.right)
        self._content.set_margin_bottom(insets.bottom)
        self._content.set_margin_start(insets.left)

    def _on_size_allocate(self, _widget: Gtk.Widget, allocation) -> None:
        self._apply_content_insets()
        self._refresh_input_region(int(allocation.width), int(allocation.height))

    def _on_realize(self, *_args) -> None:
        self._refresh_input_region(
            max(1, self.get_allocated_width()),
            max(1, self.get_allocated_height()),
            force=True,
        )

    def _on_map(self, *_args) -> None:
        self._refresh_input_region(
            max(1, self.get_allocated_width()),
            max(1, self.get_allocated_height()),
            force=True,
        )
        if self._animate and not self._tick_id:
            self._tick_id = GLib.timeout_add(40, self._on_tick)

    def _on_unmap(self, *_args) -> None:
        self._stop_tick()

    def _on_destroy(self, *_args) -> None:
        self._stop_tick()

    def _stop_tick(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def _on_tick(self) -> bool:
        self._elapsed_ms += 40
        self.queue_draw()
        return True

    def _refresh_input_region(self, width: int, height: int, *, force: bool = False) -> None:
        if width < 4 or height < 4:
            return
        key = (width, height)
        if key == self._region_key and not force:
            return
        if self.get_window() is None:
            return
        self._region_key = key
        try:
            region = input_region_from_shape(width, height, self._disguise.build_shape)
            apply_input_region(self, region)
        except Exception as error:
            print(f"Jugoo disguise: input region failed: {error}")

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        width = float(self.get_allocated_width())
        height = float(self.get_allocated_height())
        if width <= 0 or height <= 0:
            return False

        clear_to_transparent(cr)

        cr.save()
        self._disguise.build_shape(cr, width, height)
        cr.clip()
        self._disguise.render(cr, width, height, elapsed_ms=self._elapsed_ms)
        cr.restore()

        # Soft outer rim so the silhouette reads against any wallpaper.
        cr.save()
        self._disguise.build_shape(cr, width, height)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.45)
        cr.set_line_width(2.0)
        cr.stroke_preserve()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.12)
        cr.set_line_width(1.0)
        cr.stroke()
        cr.restore()
        return False
