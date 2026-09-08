"""Coordinates the Settings Center overlay with the running shell."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ..popup_handle import PopupHandle
from ..settings.manager import SettingsManager
from ..widgets.configuraciones.overlay import SettingsOverlay


class SettingsController:
    """Lazy Settings overlay; closes sibling pickers when opened."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        manager: SettingsManager,
        *,
        close_others: Callable[[], None] | None = None,
    ) -> None:
        self._manager = manager
        self._close_others = close_others
        self._overlay = PopupHandle(
            lambda: SettingsOverlay(shell_window, manager)
        )

    def toggle(self) -> None:
        window = self._overlay.get()
        if window.get_visible():
            window.close_settings()
            return
        if self._close_others is not None:
            self._close_others()
        window.open_settings()

    def close(self) -> None:
        window = self._overlay.maybe
        if window is not None:
            window.close_settings()
