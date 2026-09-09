"""Load and shape images for controlled UI slots (never use natural file size)."""

from __future__ import annotations

import math
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell

from ..window_identity import configure_interactive_popup, configure_toplevel

_IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/svg+xml",
)

_FILE_CHOOSER_WIDTH = 760
_FILE_CHOOSER_HEIGHT = 520


def choose_image_path(
    parent: Gtk.Window | None,
    *,
    title: str = "Seleccionar imagen",
) -> Path | None:
    """Open a floating layer-shell file chooser for images. Cancel → ``None``."""
    return _choose_path(parent, title=title, image_only=True)


def choose_file_path(
    parent: Gtk.Window | None,
    *,
    title: str = "Seleccionar archivo",
) -> Path | None:
    """Open a floating layer-shell file chooser. Cancel → ``None``."""
    return _choose_path(parent, title=title, image_only=False)


def _choose_path(
    parent: Gtk.Window | None,
    *,
    title: str,
    image_only: bool,
) -> Path | None:
    """Show a floating chooser above exclusive overlays (control center stays open).

    Portal/native dialogs sit below GtkLayerShell OVERLAY + EXCLUSIVE keyboard,
    so they cannot receive focus while the control center is visible. This opens
    another OVERLAY surface and temporarily releases the host keyboard grab.
    """
    host = _layer_shell_host(parent)
    previous_keyboard = None
    if host is not None and GtkLayerShell.is_layer_window(host):
        previous_keyboard = GtkLayerShell.get_keyboard_mode(host)
        GtkLayerShell.set_keyboard_mode(host, GtkLayerShell.KeyboardMode.NONE)

    state: dict[str, object] = {"path": None, "done": False}
    loop = GLib.MainLoop()

    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_name("shell-file-chooser")
    window.get_style_context().add_class("shell-picker")
    window.get_style_context().add_class("shell-file-chooser")
    configure_toplevel(window, title=title)
    configure_interactive_popup(window)
    _configure_chooser_layer_shell(window)

    backdrop = Gtk.EventBox()
    backdrop.get_style_context().add_class("launcher-backdrop")
    backdrop.connect(
        "button-press-event",
        lambda _w, event: _cancel_from_backdrop(event, finish),
    )
    window.add(backdrop)

    aligner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    aligner.set_halign(Gtk.Align.CENTER)
    aligner.set_valign(Gtk.Align.CENTER)
    backdrop.add(aligner)

    card = Gtk.EventBox()
    card.get_style_context().add_class("launcher-card-host")
    card.connect("button-press-event", lambda _w, event: event.button == 1)
    aligner.pack_start(card, False, False, 0)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    outer.get_style_context().add_class("launcher-card")
    outer.get_style_context().add_class("shell-file-chooser-card")
    outer.set_size_request(_FILE_CHOOSER_WIDTH, _FILE_CHOOSER_HEIGHT)
    card.add(outer)

    heading = Gtk.Label(label=title, xalign=0)
    heading.get_style_context().add_class("settings-page-title")
    outer.pack_start(heading, False, False, 0)

    chooser = Gtk.FileChooserWidget(action=Gtk.FileChooserAction.OPEN)
    chooser.set_hexpand(True)
    chooser.set_vexpand(True)
    chooser.get_style_context().add_class("shell-file-chooser-widget")
    if image_only:
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Imágenes")
        for mime in _IMAGE_MIME_TYPES:
            image_filter.add_mime_type(mime)
        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.bmp", "*.svg"):
            image_filter.add_pattern(pattern)
        chooser.add_filter(image_filter)
        chooser.set_filter(image_filter)
        any_filter = Gtk.FileFilter()
        any_filter.set_name("Todos los archivos")
        any_filter.add_pattern("*")
        chooser.add_filter(any_filter)
    outer.pack_start(chooser, True, True, 0)

    buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    buttons.set_halign(Gtk.Align.END)
    cancel = Gtk.Button(label="Cancelar")
    cancel.get_style_context().add_class("settings-browse-button")
    open_btn = Gtk.Button(label="Abrir")
    open_btn.get_style_context().add_class("settings-browse-button")
    buttons.pack_start(cancel, False, False, 0)
    buttons.pack_start(open_btn, False, False, 0)
    outer.pack_start(buttons, False, False, 0)

    def finish(path: Path | None) -> None:
        if state["done"]:
            return
        state["done"] = True
        state["path"] = path
        if loop.is_running():
            loop.quit()

    def accept(*_args) -> None:
        filename = chooser.get_filename()
        if not filename:
            return
        path = Path(filename)
        if path.is_file():
            finish(path)

    cancel.connect("clicked", lambda *_a: finish(None))
    open_btn.connect("clicked", accept)
    chooser.connect("file-activated", accept)
    window.connect(
        "key-press-event",
        lambda _w, event: _on_chooser_key(event, finish),
    )
    window.add_events(Gdk.EventMask.KEY_PRESS_MASK)

    window.show_all()
    window.present()
    GLib.idle_add(chooser.grab_focus)

    try:
        loop.run()
    finally:
        if window.get_realized():
            window.hide()
            window.destroy()
        if host is not None and previous_keyboard is not None:
            GtkLayerShell.set_keyboard_mode(host, previous_keyboard)
            if host.get_visible():
                host.present()

    path = state["path"]
    return path if isinstance(path, Path) else None


def _configure_chooser_layer_shell(window: Gtk.Window) -> None:
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "shell-file-chooser")
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    for edge in (
        GtkLayerShell.Edge.TOP,
        GtkLayerShell.Edge.BOTTOM,
        GtkLayerShell.Edge.LEFT,
        GtkLayerShell.Edge.RIGHT,
    ):
        GtkLayerShell.set_anchor(window, edge, True)


def _layer_shell_host(parent: Gtk.Window | None) -> Gtk.Window | None:
    if parent is None:
        return None
    toplevel = parent.get_toplevel()
    if isinstance(toplevel, Gtk.Window):
        return toplevel
    return parent if isinstance(parent, Gtk.Window) else None


def _cancel_from_backdrop(event: Gdk.EventButton, finish) -> bool:
    if event.button != 1:
        return False
    finish(None)
    return True


def _on_chooser_key(event: Gdk.EventKey, finish) -> bool:
    key_name = Gdk.keyval_name(event.keyval) or ""
    if key_name in {"Escape", "Esc"}:
        finish(None)
        return True
    return False


def load_cover_pixbuf(path: Path, size: int) -> GdkPixbuf.Pixbuf | None:
    """Scale/crop to a square of ``size``×``size`` (cover). File size never leaks."""
    try:
        original = GdkPixbuf.Pixbuf.new_from_file(str(path))
    except Exception:
        return None
    width = original.get_width()
    height = original.get_height()
    if width <= 0 or height <= 0:
        return None
    scale = max(size / width, size / height)
    scaled_w = max(1, int(round(width * scale)))
    scaled_h = max(1, int(round(height * scale)))
    scaled = original.scale_simple(scaled_w, scaled_h, GdkPixbuf.InterpType.BILINEAR)
    if scaled is None:
        return None
    x = max(0, (scaled_w - size) // 2)
    y = max(0, (scaled_h - size) // 2)
    crop_w = min(size, scaled_w)
    crop_h = min(size, scaled_h)
    cropped = scaled.new_subpixbuf(x, y, crop_w, crop_h)
    if cropped is None:
        return None
    # Copy so the parent scaled pixbuf can be released.
    return cropped.copy()


def circular_pixbuf(square: GdkPixbuf.Pixbuf, size: int) -> GdkPixbuf.Pixbuf | None:
    """Clip a square pixbuf into a perfect circle with alpha."""
    if square.get_width() != size or square.get_height() != size:
        square = square.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if square is None:
            return None
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.arc(size / 2.0, size / 2.0, size / 2.0, 0.0, 2.0 * math.pi)
    cr.clip()
    Gdk.cairo_set_source_pixbuf(cr, square, 0, 0)
    cr.paint()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def rounded_pixbuf(square: GdkPixbuf.Pixbuf, size: int, radius: float) -> GdkPixbuf.Pixbuf | None:
    """Clip a square pixbuf into a rounded rectangle."""
    if square.get_width() != size or square.get_height() != size:
        square = square.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if square is None:
            return None
    radius = max(0.0, min(radius, size / 2.0))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    _rounded_rect(cr, 0, 0, size, size, radius)
    cr.clip()
    Gdk.cairo_set_source_pixbuf(cr, square, 0, 0)
    cr.paint()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _rounded_rect(
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    radius: float,
) -> None:
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
