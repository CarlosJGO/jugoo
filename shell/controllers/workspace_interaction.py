"""Intermediate controller coordinating hover popup, right-click panel, and pointer grace."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ..config import WORKSPACE_HOVER_DELAY_MS, WORKSPACE_HOVER_GRACE_MS
from ..eventbus import EventBus
from ..models import AudioSnapshot, WorkspaceAudioState
from ..servicios.audio.audio import AUDIO_CHANGED, AudioService
from ..popup_handle import PopupHandle, PopupOutsideDismiss, pointer_inside_widget
from ..widgets.barra.workspace import (
    WORKSPACE_AUDIO_REQUESTED,
    WORKSPACE_HOVER_ENDED,
    WORKSPACE_HOVER_STARTED,
    WorkspaceWidget,
)
from ..widgets.espacios_trabajo.workspace_audio_popup import WorkspaceAudioPopup
from ..widgets.espacios_trabajo.workspace_panel import WorkspacePanel


class WorkspaceInteractionController:
    """Hover → WorkspaceAudioPopup; right-click → WorkspacePanel; left-click stays in widget."""

    def __init__(
        self,
        event_bus: EventBus,
        audio_service: AudioService,
        workspace_widget: WorkspaceWidget,
        shell_window: Gtk.Window,
    ) -> None:
        self._event_bus = event_bus
        self._audio_service = audio_service
        self._workspace_widget = workspace_widget
        self._shell_window = shell_window
        self._shell_press_bound = False

        self._hovered_workspace_id: int | None = None
        self._hover_button_widget: Gtk.Widget | None = None
        self._hover_timer_id: int | None = None
        self._close_timer_id: int | None = None
        self._panel_outside_click = PopupOutsideDismiss()

        self._popup = PopupHandle(
            lambda: WorkspaceAudioPopup(
                shell_window,
                on_volume_change=self._audio_service.set_stream_volume,
                on_mute_toggle=self._audio_service.toggle_stream_mute,
                on_playback_device_change=self._audio_service.set_playback_sink,
                on_capture_device_change=self._audio_service.set_capture_source,
                on_pointer_enter=self._on_popup_pointer_enter,
                on_pointer_leave=self._schedule_hover_close,
            )
        )

        self._panel = PopupHandle(
            lambda: WorkspacePanel(
                shell_window,
                on_volume_change=self._audio_service.set_stream_volume,
                on_mute_toggle=self._audio_service.toggle_stream_mute,
                on_playback_device_change=self._audio_service.set_playback_sink,
                on_capture_device_change=self._audio_service.set_capture_source,
            )
        )

        self._event_bus.subscribe(WORKSPACE_HOVER_STARTED, self._on_hover_started)
        self._event_bus.subscribe(WORKSPACE_HOVER_ENDED, self._on_hover_ended)
        self._event_bus.subscribe(WORKSPACE_AUDIO_REQUESTED, self._on_audio_requested)
        self._event_bus.subscribe(AUDIO_CHANGED, self._on_audio_changed)

    def close_workspace_audio_popup(self) -> None:
        self._cancel_hover_timer()
        self._cancel_close_timer()
        self._hovered_workspace_id = None
        self._hover_button_widget = None
        popup = self._popup.maybe
        if popup is not None:
            popup.hide_popup()

    def close_workspace_panel(self) -> None:
        self._panel_outside_click.uninstall()
        panel = self._panel.maybe
        if panel is not None:
            panel.close_panel()

    def toggle_workspace_panel(self, workspace_id: int) -> None:
        button = self._workspace_widget.get_button(workspace_id)
        if button is None:
            return
        self._audio_service.refresh()
        snapshot = self._audio_service.snapshot
        audio_state = snapshot.get_workspace_audio(workspace_id)
        self._open_or_toggle_panel(button, workspace_id, audio_state, snapshot)

    def _ensure_shell_press_handler(self) -> None:
        if self._shell_press_bound:
            return
        self._shell_window.connect("button-press-event", self._on_shell_button_press)
        self._shell_press_bound = True

    def _on_shell_button_press(self, _window: Gtk.Widget, event: Gdk.EventButton) -> bool:
        panel = self._panel.maybe
        if panel is None or not panel.get_visible() or event.button not in (1, 3):
            return False
        if panel.pointer_is_inside():
            return False
        self.close_workspace_panel()
        return False

    def _on_hover_started(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return

        workspace_id = payload.get("workspace_id")
        widget = payload.get("widget")
        if workspace_id is None or widget is None:
            return

        self._cancel_hover_timer()
        self._cancel_close_timer()
        self._hovered_workspace_id = workspace_id
        self._hover_button_widget = widget

        self._hover_timer_id = GLib.timeout_add(
            WORKSPACE_HOVER_DELAY_MS,
            self._on_hover_timer_triggered,
            workspace_id,
        )

    def _on_hover_ended(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return

        workspace_id = payload.get("workspace_id")
        if workspace_id != self._hovered_workspace_id:
            return

        self._schedule_hover_close()

    def _on_popup_pointer_enter(self) -> None:
        self._cancel_close_timer()

    def _schedule_hover_close(self) -> None:
        self._cancel_close_timer()
        self._close_timer_id = GLib.timeout_add(
            WORKSPACE_HOVER_GRACE_MS,
            self._on_close_timer_triggered,
        )

    def _on_close_timer_triggered(self) -> bool:
        self._close_timer_id = None
        popup = self._popup.maybe
        if popup is None:
            return False
        if popup.pointer_is_inside():
            return False
        if self._hover_button_widget is not None and pointer_inside_widget(
            self._hover_button_widget
        ):
            return False
        self.close_workspace_audio_popup()
        return False

    def _on_hover_timer_triggered(self, workspace_id: int) -> bool:
        self._hover_timer_id = None
        if self._hovered_workspace_id != workspace_id:
            return False

        self._audio_service.refresh()
        snapshot = self._audio_service.snapshot
        audio_state = snapshot.get_workspace_audio(workspace_id)
        if not audio_state.has_audio:
            return False
        if self._hover_button_widget is None:
            return False

        self._popup.get().show_for(self._hover_button_widget, audio_state, snapshot)
        return False

    def _on_audio_requested(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return

        workspace_id = payload.get("workspace_id")
        button = payload.get("widget")
        if workspace_id is None or button is None:
            return

        self.close_workspace_audio_popup()
        self._audio_service.refresh()
        snapshot = self._audio_service.snapshot
        audio_state = snapshot.get_workspace_audio(workspace_id)
        self._open_or_toggle_panel(button, workspace_id, audio_state, snapshot)

    def _open_or_toggle_panel(
        self,
        button: Gtk.Widget,
        workspace_id: int,
        audio_state: WorkspaceAudioState | None = None,
        snapshot: AudioSnapshot | None = None,
    ) -> None:
        panel = self._panel.maybe
        if (
            panel is not None
            and panel.get_visible()
            and panel.workspace_id == workspace_id
        ):
            self.close_workspace_panel()
            return

        self._ensure_shell_press_handler()
        panel = self._panel.get()
        panel.open_for(button, workspace_id, audio_state, snapshot)
        self._panel_outside_click.install(
            panel,
            self._shell_window,
            (button,),
            self.close_workspace_panel,
            self._event_bus,
        )

    def _on_audio_changed(self, snapshot: AudioSnapshot) -> None:
        GLib.idle_add(self._sync_popup_and_panel_audio, snapshot)

    def _sync_popup_and_panel_audio(self, snapshot: AudioSnapshot) -> bool:
        popup = self._popup.maybe
        if popup is not None and popup.workspace_id is not None and popup.get_visible():
            audio_state = snapshot.get_workspace_audio(popup.workspace_id)
            if audio_state.has_audio:
                popup.update_streams(audio_state, snapshot)
            else:
                popup.hide_popup()

        panel = self._panel.maybe
        if panel is not None and panel.workspace_id is not None and panel.get_visible():
            audio_state = snapshot.get_workspace_audio(panel.workspace_id)
            panel.sync_audio(audio_state, snapshot)

        return False

    def _cancel_hover_timer(self) -> None:
        if self._hover_timer_id is not None:
            GLib.source_remove(self._hover_timer_id)
            self._hover_timer_id = None

    def _cancel_close_timer(self) -> None:
        if self._close_timer_id is not None:
            GLib.source_remove(self._close_timer_id)
            self._close_timer_id = None
