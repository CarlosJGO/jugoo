"""Volume OSD controller: one window, hide delay after last default-sink change."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GLib", "2.0")

from gi.repository import GLib, Gtk

from ..config import VOLUME_OSD_HIDE_DELAY_MS
from ..eventbus import EventBus
from ..models import AudioSnapshot, SystemVolumeState
from ..popup_handle import PopupHandle
from ..servicios.audio.audio import AUDIO_CHANGED, AudioService
from ..widgets.audio.volume_osd import VolumeOsd


class VolumeOsdController:
    """Shows/updates a single non-interactive volume OSD from AudioService."""

    def __init__(
        self,
        event_bus: EventBus,
        audio_service: AudioService,
        shell_window: Gtk.Window,
        *,
        hide_delay_ms: int = VOLUME_OSD_HIDE_DELAY_MS,
    ) -> None:
        self._event_bus = event_bus
        self._audio_service = audio_service
        self._shell_window = shell_window
        self._hide_delay_ms = hide_delay_ms
        self._last_volume: SystemVolumeState | None = None
        self._started = False
        self._hide_source_id = 0
        self._osd = PopupHandle(lambda: VolumeOsd(shell_window))

        self._event_bus.subscribe(AUDIO_CHANGED, self._on_audio_changed)

    @property
    def hide_delay_ms(self) -> int:
        return self._hide_delay_ms

    def start(self) -> None:
        """Baseline current volume without showing the OSD."""
        snapshot = self._audio_service.snapshot
        self._last_volume = snapshot.system_volume
        self._started = True

    def close(self) -> None:
        self._event_bus.unsubscribe(AUDIO_CHANGED, self._on_audio_changed)
        self._cancel_hide()
        self._osd.hide()

    def _on_audio_changed(self, snapshot: AudioSnapshot) -> None:
        GLib.idle_add(self._handle_audio_changed, snapshot)

    def _handle_audio_changed(self, snapshot: AudioSnapshot) -> bool:
        state = snapshot.system_volume
        if state is None:
            return False
        if not self._started:
            self._last_volume = state
            self._started = True
            return False
        if state == self._last_volume:
            return False
        self._last_volume = state
        self._show_or_update(state)
        return False

    def _show_or_update(self, state: SystemVolumeState) -> None:
        osd = self._osd.get()
        osd.refresh(state)
        osd.show_osd()
        self._restart_hide_timer()

    def _restart_hide_timer(self) -> None:
        self._cancel_hide()
        self._hide_source_id = GLib.timeout_add(
            self._hide_delay_ms,
            self._hide_idle,
        )

    def _hide_idle(self) -> bool:
        self._hide_source_id = 0
        osd = self._osd.maybe
        if osd is not None:
            osd.hide_osd()
        return False

    def _cancel_hide(self) -> None:
        if self._hide_source_id:
            GLib.source_remove(self._hide_source_id)
            self._hide_source_id = 0
