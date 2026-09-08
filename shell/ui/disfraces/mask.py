"""RGBA surfaces, silhouette masks, and pointer input regions."""

from __future__ import annotations

from collections.abc import Callable

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, Gtk

ShapeBuilder = Callable[[cairo.Context, float, float], None]


def prepare_transparent_toplevel(window: Gtk.Window) -> None:
    """Enable per-pixel alpha on a toplevel. Must run before the window realizes."""
    screen = window.get_screen()
    if screen is None:
        return
    visual = screen.get_rgba_visual()
    if visual is not None:
        window.set_visual(visual)
    window.set_app_paintable(True)
    window.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))


def clear_to_transparent(cr: cairo.Context) -> None:
    cr.save()
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.restore()


def mask_surface_from_shape(
    width: int,
    height: int,
    build_shape: ShapeBuilder,
) -> cairo.ImageSurface:
    """A8 mask: opaque where the silhouette exists."""
    width = max(1, width)
    height = max(1, height)
    surface = cairo.ImageSurface(cairo.FORMAT_A8, width, height)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.set_source_rgba(1, 1, 1, 1)
    build_shape(cr, float(width), float(height))
    cr.fill()
    surface.flush()
    return surface


def input_region_from_shape(
    width: int,
    height: int,
    build_shape: ShapeBuilder,
) -> cairo.Region:
    """Pointer-hit region matching the visible silhouette."""
    mask = mask_surface_from_shape(width, height, build_shape)
    # GDK builds a region from non-transparent pixels of an A8/ARGB surface.
    return Gdk.cairo_region_create_from_surface(mask)


def apply_input_region(widget: Gtk.Widget, region: cairo.Region) -> None:
    gdk_window = widget.get_window()
    if gdk_window is None:
        return
    gdk_window.input_shape_combine_region(region, 0, 0)
