"""Parse freedesktop notification hints and cache image payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import GdkPixbuf, GLib


def normalize_hints(raw_hints: dict[Any, Any] | None) -> dict[str, Any]:
    if not raw_hints:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in raw_hints.items():
        normalized[str(key)] = _unpack_variant(value)
    return normalized


def urgency_from_hints(hints: dict[str, Any]) -> int:
    value = _unpack_variant(hints.get("urgency"))
    if value is None:
        return 1
    try:
        urgency = int(value)
    except (TypeError, ValueError):
        return 1
    return max(0, min(2, urgency))


def resolve_icon_fields(
    *,
    app_icon: str,
    hints: dict[str, Any],
    notification_id: int,
    cache_dir: Path,
) -> tuple[str, str, str, str]:
    """Return ``(icon_name, image_path, normalized_app_icon, desktop_entry)``."""
    icon_name = _hint_string(hints, "icon-name", "desktop-entry")
    desktop_entry = _hint_string(hints, "desktop-entry")
    image_path = _hint_string(hints, "image-path")

    cached = _cache_image_data(hints.get("image-data"), notification_id, cache_dir)
    if cached:
        image_path = cached

    normalized_app_icon = str(app_icon or "").strip()
    if normalized_app_icon.startswith("/"):
        path = Path(normalized_app_icon).expanduser()
        if path.is_file():
            if not image_path:
                image_path = str(path)
            normalized_app_icon = ""

    if not icon_name and normalized_app_icon and not normalized_app_icon.startswith("/"):
        icon_name = normalized_app_icon

    return icon_name, image_path, normalized_app_icon, desktop_entry


def _hint_string(hints: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = hints.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _cache_image_data(
    payload: Any,
    notification_id: int,
    cache_dir: Path,
) -> str:
    if payload is None:
        return ""

    if isinstance(payload, GLib.Variant):
        payload = payload.unpack()

    if not isinstance(payload, tuple) or len(payload) < 7:
        return ""

    width, height, rowstride, has_alpha, _bits, _channels, data = payload[:7]
    if width <= 0 or height <= 0 or not data:
        return ""

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{notification_id}.png"
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(data)),
            GdkPixbuf.Colorspace.RGB,
            bool(has_alpha),
            8,
            int(width),
            int(height),
            int(rowstride),
        )
        pixbuf.savev(str(target), "png", [], [])
        return str(target)
    except (GLib.Error, OSError, TypeError, ValueError) as error:
        print(f"shell: notifications: could not cache image-data: {error}")
        return ""


def _unpack_variant(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value
