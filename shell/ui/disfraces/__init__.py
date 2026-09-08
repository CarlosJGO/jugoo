"""Spatial window costumes: shape mask + render + GTK content pocket."""

from __future__ import annotations

from .apply import dress_content, dress_widget, dress_window
from .catalog import all_assignments, disguise_for, set_disguise
from .roles import DisguiseId, WindowRole

__all__ = [
    "DisguiseId",
    "WindowRole",
    "all_assignments",
    "disguise_for",
    "dress_content",
    "dress_widget",
    "dress_window",
    "set_disguise",
]
