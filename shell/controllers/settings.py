"""Routes Settings entry points into the unified control center."""

from __future__ import annotations

from collections.abc import Callable

from ..settings.schema import CategoryId


class SettingsController:
    """Entry point used by bar/action/CLI to open center in settings mode."""

    def __init__(
        self,
        *,
        open_settings: Callable[[CategoryId], None],
        close_center: Callable[[], None],
    ) -> None:
        self._open_settings = open_settings
        self._close_center = close_center

    def toggle(self) -> None:
        self._open_settings(CategoryId.GENERAL)

    def close(self) -> None:
        self._close_center()
