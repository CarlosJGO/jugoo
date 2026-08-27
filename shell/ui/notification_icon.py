"""Resolve and display notification icons from D-Bus hint fields."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import GdkPixbuf, GLib, Gtk

from ..models import NotificationSnapshot

_FALLBACK_ICON = "dialog-information-symbolic"


def apply_notification_icon(
    image: Gtk.Image,
    snapshot: NotificationSnapshot,
    *,
    pixel_size: int,
) -> None:
    """Fill ``image`` using image-path, icon hints, or a generic fallback."""
    for path in _candidate_paths(snapshot):
        pixbuf = _pixbuf_from_path(path, pixel_size)
        if pixbuf is not None:
            image.set_from_pixbuf(pixbuf)
            image.set_pixel_size(pixel_size)
            return

    for icon_name in _candidate_icon_names(snapshot):
        if _set_themed_icon(image, icon_name, pixel_size):
            return

    _set_themed_icon(image, _FALLBACK_ICON, pixel_size)


def _candidate_paths(snapshot: NotificationSnapshot) -> tuple[str, ...]:
    paths: list[str] = []
    for value in (snapshot.image_path, snapshot.app_icon):
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file():
            paths.append(str(path))
    return tuple(paths)


def _candidate_icon_names(snapshot: NotificationSnapshot) -> tuple[str, ...]:
    names: list[str] = []
    for value in (snapshot.icon_name, snapshot.app_icon):
        if not value or value.startswith("/"):
            continue
        normalized = value.strip()
        if normalized and normalized not in names:
            names.append(normalized)
    return tuple(names)


def _pixbuf_from_path(path: str, pixel_size: int) -> GdkPixbuf.Pixbuf | None:
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(path, pixel_size, pixel_size)
    except GLibError:
        return None


def _set_themed_icon(image: Gtk.Image, icon_name: str, pixel_size: int) -> bool:
    theme = Gtk.IconTheme.get_default()
    if not theme.has_icon(icon_name):
        return False
    image.set_from_icon_name(icon_name, Gtk.IconSize.MENU)
    image.set_pixel_size(pixel_size)
    return True


GLibError = GLib.Error
