"""Coordinates Organic Islands without coupling to domain services."""

from __future__ import annotations

from .host import OrganicIslandHost
from .stage import IslandStage


class IslandController:
    """One active island at a time in v1."""

    def __init__(self, stage: IslandStage) -> None:
        self._stage = stage
        self._hosts: dict[str, OrganicIslandHost] = {}
        self._active_id: str | None = None

    @property
    def stage(self) -> IslandStage:
        return self._stage

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def register(self, host: OrganicIslandHost) -> None:
        self._hosts[host.island_id] = host

    def unregister(self, island_id: str) -> None:
        self._hosts.pop(island_id, None)
        if self._active_id == island_id:
            self._active_id = None

    def get(self, island_id: str) -> OrganicIslandHost | None:
        return self._hosts.get(island_id)

    def open(self, island_id: str) -> None:
        host = self._hosts.get(island_id)
        if host is None:
            return
        if self._active_id and self._active_id != island_id:
            other = self._hosts.get(self._active_id)
            if other is not None and other.is_open:
                other.close()
        self._active_id = island_id
        host.open()

    def close(self, island_id: str | None = None) -> None:
        target = island_id or self._active_id
        if target is None:
            return
        host = self._hosts.get(target)
        if host is None:
            return
        host.close()

    def toggle(self, island_id: str) -> None:
        host = self._hosts.get(island_id)
        if host is None:
            return
        if host.state.value == "active":
            self.close(island_id)
            return
        if host.state.value == "attached":
            self.open(island_id)

    def note_closed(self, island_id: str) -> None:
        if self._active_id == island_id:
            self._active_id = None
