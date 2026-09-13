"""Lifecycle states for an Organic Island."""

from __future__ import annotations

from enum import Enum


class IslandState(Enum):
    ATTACHED = "attached"
    DETACHING = "detaching"
    EXPANDING = "expanding"
    ACTIVE = "active"
    COLLAPSING = "collapsing"
    RETURNING = "returning"

    @property
    def is_busy(self) -> bool:
        return self in {
            IslandState.DETACHING,
            IslandState.EXPANDING,
            IslandState.COLLAPSING,
            IslandState.RETURNING,
        }

    @property
    def is_detached(self) -> bool:
        return self in {
            IslandState.DETACHING,
            IslandState.EXPANDING,
            IslandState.ACTIVE,
            IslandState.COLLAPSING,
            IslandState.RETURNING,
        }
