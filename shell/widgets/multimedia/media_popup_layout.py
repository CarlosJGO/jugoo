"""Helpers for fixed-size media popup layout."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf

from ...config import MEDIA_ARTWORK_SIZE, MEDIA_POPUP_MAX_HEIGHT, MEDIA_POPUP_WIDTH


def media_popup_dimensions() -> tuple[int, int, int]:
    """Return fixed popup width, max height, and artwork size."""
    return MEDIA_POPUP_WIDTH, MEDIA_POPUP_MAX_HEIGHT, MEDIA_ARTWORK_SIZE


def scale_artwork_pixbuf(pixbuf: GdkPixbuf.Pixbuf, size: int) -> GdkPixbuf.Pixbuf:
    """Scale artwork to cover a square (crop overflow) for player chrome."""
    width = int(pixbuf.get_width())
    height = int(pixbuf.get_height())
    if width <= 0 or height <= 0:
        return pixbuf
    scale = max(size / width, size / height)
    target_w = max(1, int(width * scale))
    target_h = max(1, int(height * scale))
    scaled = pixbuf.scale_simple(
        target_w,
        target_h,
        GdkPixbuf.InterpType.BILINEAR,
    )
    if scaled is None:
        return pixbuf
    x = max(0, (target_w - size) // 2)
    y = max(0, (target_h - size) // 2)
    return scaled.new_subpixbuf(x, y, size, size)
