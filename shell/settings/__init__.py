"""Centralized, persistent, extensible user settings for Jugoo."""

from __future__ import annotations

from .manager import SETTINGS_CHANGED, SettingsManager
from .schema import APPLY_LIVE, APPLY_RELOAD, APPLY_RESTART, CategoryId, SettingDef

__all__ = [
    "APPLY_LIVE",
    "APPLY_RELOAD",
    "APPLY_RESTART",
    "SETTINGS_CHANGED",
    "CategoryId",
    "SettingDef",
    "SettingsManager",
]
