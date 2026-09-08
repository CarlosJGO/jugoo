"""Which surface wears which costume — the assignment board.

Shaped costumes (asteroid/meteor) remain implemented under
``shell/ui/disfraces/`` but are inactive until we revisit the look.
Wire them back here (or via ``set_disguise``) when ready.
"""

from __future__ import annotations

from .roles import DisguiseId, WindowRole

# All roles use NORMAL for now — keep windows conventional.
_DISGUISE_MAP: dict[WindowRole, DisguiseId] = {
    WindowRole.NOTIFICATIONS_POPUP: DisguiseId.NORMAL,
    WindowRole.NOTIFICATION_TOAST: DisguiseId.NORMAL,
    WindowRole.NOTIFICATION_GROUP: DisguiseId.NORMAL,
    WindowRole.MEDIA_POPUP: DisguiseId.NORMAL,
    WindowRole.TASKS_POPUP: DisguiseId.NORMAL,
    WindowRole.CONTROL_CENTER: DisguiseId.NORMAL,
    WindowRole.SETTINGS: DisguiseId.NORMAL,
    WindowRole.LAUNCHER: DisguiseId.NORMAL,
    WindowRole.CLIPBOARD: DisguiseId.NORMAL,
    WindowRole.EMOJI: DisguiseId.NORMAL,
    WindowRole.POWER_MENU: DisguiseId.NORMAL,
    WindowRole.VOLUME_OSD: DisguiseId.NORMAL,
    WindowRole.GENERIC: DisguiseId.NORMAL,
}

# Future puestos (dormant):
# WindowRole.NOTIFICATIONS_POPUP: DisguiseId.ASTEROID,
# WindowRole.NOTIFICATION_TOAST: DisguiseId.METEOR,
# WindowRole.NOTIFICATION_GROUP: DisguiseId.ASTEROID,


def disguise_for(role: WindowRole) -> DisguiseId:
    return _DISGUISE_MAP.get(role, DisguiseId.NORMAL)


def set_disguise(role: WindowRole, disguise: DisguiseId) -> None:
    """Runtime override — useful later from Configuraciones."""
    _DISGUISE_MAP[role] = disguise


def all_assignments() -> dict[WindowRole, DisguiseId]:
    return dict(_DISGUISE_MAP)
