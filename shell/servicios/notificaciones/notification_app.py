"""Stable application identifiers for notification settings."""

from __future__ import annotations


def notification_app_key(*, app_name: str, app_icon: str = "") -> str:
    """Return a stable, case-insensitive key for per-app notification settings."""
    name = str(app_name or "").strip()
    if name:
        return name.casefold()

    icon = str(app_icon or "").strip()
    if icon.endswith(".desktop"):
        icon = icon[: -len(".desktop")]
    if icon:
        return icon.casefold()

    return "unknown"
