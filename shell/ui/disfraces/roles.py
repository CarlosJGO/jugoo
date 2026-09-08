"""Roles that identify Jugoo surfaces eligible for a spatial disguise."""

from __future__ import annotations

from enum import Enum


class WindowRole(str, Enum):
    """Stable identity for a shell surface — not a GTK class name."""

    NOTIFICATIONS_POPUP = "notifications_popup"
    NOTIFICATION_TOAST = "notification_toast"
    NOTIFICATION_GROUP = "notification_group"
    MEDIA_POPUP = "media_popup"
    TASKS_POPUP = "tasks_popup"
    CONTROL_CENTER = "control_center"
    SETTINGS = "settings"
    LAUNCHER = "launcher"
    CLIPBOARD = "clipboard"
    EMOJI = "emoji"
    POWER_MENU = "power_menu"
    VOLUME_OSD = "volume_osd"
    GENERIC = "generic"


class DisguiseId(str, Enum):
    """Visual costume. ``NORMAL`` is the undecorated panel chrome."""

    NORMAL = "normal"
    ASTEROID = "asteroid"
    METEOR = "meteor"
