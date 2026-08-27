"""Media bar block click → popup with MPRIS transport controls."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from ..eventbus import EventBus
from ..popup_handle import PopupHandle, PopupOutsideDismiss
from ..servicios.multimedia.media import MEDIA_CHANGED, MediaService
from ..widgets.barra.active_window import MEDIA_BAR_CLICKED, ActiveWindowWidget
from ..widgets.multimedia.media_popup import MediaPopup


class MediaController:
    """Opens the media popup from the active-window block and keeps it in sync."""

    def __init__(
        self,
        event_bus: EventBus,
        media_service: MediaService,
        active_window_widget: ActiveWindowWidget,
        shell_window: Gtk.Window,
    ) -> None:
        self._event_bus = event_bus
        self._service = media_service
        self._active_window_widget = active_window_widget
        self._shell_window = shell_window
        self._outside_click = PopupOutsideDismiss()
        self._popup = PopupHandle(
            lambda: MediaPopup(shell_window, media_service),
        )

        self._event_bus.subscribe(MEDIA_BAR_CLICKED, self._on_media_bar_clicked)
        self._event_bus.subscribe(MEDIA_CHANGED, self._on_media_changed)

    def close_popup(self) -> None:
        self._outside_click.uninstall()
        popup = self._popup.maybe
        if popup is not None:
            popup.close_popup()

    def toggle_popup(self) -> None:
        if not self._service.snapshot.has_media:
            return
        if self._popup.is_visible():
            self.close_popup()
            return
        self._open_popup()

    def _open_popup(self) -> None:
        popup = self._popup.get()
        anchor = self._active_window_widget.get_anchor_widget()
        popup.open_for(anchor)
        self._outside_click.install(
            popup,
            self._shell_window,
            (anchor,),
            self.close_popup,
            self._event_bus,
        )

    def _on_media_bar_clicked(self, _widget: ActiveWindowWidget) -> None:
        GLib.idle_add(self._handle_media_bar_clicked)

    def _handle_media_bar_clicked(self) -> bool:
        self.toggle_popup()
        return False

    def _on_media_changed(self, snapshot) -> None:
        self._handle_media_changed(snapshot)

    def _handle_media_changed(self, snapshot) -> bool:
        if not snapshot.has_media:
            self.close_popup()
            return False
        popup = self._popup.maybe
        if popup is not None and popup.get_visible():
            popup.refresh(snapshot)
        return False
