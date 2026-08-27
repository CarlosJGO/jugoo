"""Workspace audio hover popup."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, Gtk

from ...config import WORKSPACE_POPUP_OFFSET
from ...popup_handle import present_popup, hide_popup as fade_hide_popup
from ...models import AudioDevice, AudioSnapshot, WorkspaceAudioState
from ...window_identity import (
    TITLE_WORKSPACE_AUDIO,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from ..audio.audio_stream_row import AudioStreamRow, group_streams_by_app


class WorkspaceAudioPopup(Gtk.Window):
    """Level-1 hover popup: a normal GTK window positioned under the workspace block."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        on_volume_change=None,
        on_mute_toggle=None,
        on_playback_device_change=None,
        on_capture_device_change=None,
        on_pointer_enter=None,
        on_pointer_leave=None,
    ):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self.workspace_id: int | None = None
        self._rows: dict[str, AudioStreamRow] = {}
        self._output_devices: tuple[AudioDevice, ...] = ()
        self._input_devices: tuple[AudioDevice, ...] = ()
        self._on_volume_change = on_volume_change
        self._on_mute_toggle = on_mute_toggle
        self._on_playback_device_change = on_playback_device_change
        self._on_capture_device_change = on_capture_device_change
        self._on_pointer_enter = on_pointer_enter
        self._on_pointer_leave = on_pointer_leave
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None

        self.set_name("shell-workspace-audio-popup")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_WORKSPACE_AUDIO)
        configure_interactive_popup(self)

        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("enter-notify-event", self._mouse_enter)
        self.connect("leave-notify-event", self._mouse_leave)

        self._container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._container.get_style_context().add_class("workspace-audio-popup-content")
        self.add(self._container)

    def show_for(
        self,
        button: Gtk.Widget,
        audio_state: WorkspaceAudioState,
        snapshot: AudioSnapshot | None = None,
    ) -> None:
        self._anchor_button = button
        self._fixed_popup_top = None
        self.workspace_id = audio_state.workspace_id
        if snapshot is not None:
            self._output_devices = snapshot.output_devices
            self._input_devices = snapshot.input_devices
        self._render(audio_state.streams)
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def update_streams(
        self,
        audio_state: WorkspaceAudioState,
        snapshot: AudioSnapshot | None = None,
    ) -> None:
        if not self.get_visible() or self.is_adjusting_volume():
            return
        if snapshot is not None:
            self._output_devices = snapshot.output_devices
            self._input_devices = snapshot.input_devices
        self._render(audio_state.streams)
        if self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def is_adjusting_volume(self) -> bool:
        return any(row.is_dragging for row in self._rows.values())

    def hide_popup(self) -> None:
        self.workspace_id = None
        self._anchor_button = None
        self._fixed_popup_top = None
        fade_hide_popup(self)

    def _position_after_show(self) -> bool:
        if self._anchor_button is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor_button,
                title=TITLE_WORKSPACE_AUDIO,
                offset=WORKSPACE_POPUP_OFFSET,
                fixed_top=self._fixed_popup_top,
            )
            if self._fixed_popup_top is None and top is not None:
                self._fixed_popup_top = top
        return False

    def _render(self, streams: tuple) -> None:
        groups = group_streams_by_app(streams)
        active_keys = {row_key for row_key, _playback, _capture in groups}

        for row_key, row in list(self._rows.items()):
            if row_key not in active_keys:
                self._container.remove(row)
                del self._rows[row_key]

        for row_key, playback, capture in groups:
            if row_key in self._rows:
                if not self._rows[row_key].is_dragging:
                    self._rows[row_key].update_group(
                        playback,
                        capture,
                        self._output_devices,
                        self._input_devices,
                    )
            else:
                row = AudioStreamRow(
                    row_key,
                    playback,
                    capture,
                    self._output_devices,
                    self._input_devices,
                    on_volume_change=self._on_volume_change,
                    on_mute_toggle=self._on_mute_toggle,
                    on_playback_device_change=self._on_playback_device_change,
                    on_capture_device_change=self._on_capture_device_change,
                )
                self._rows[row_key] = row
                self._container.pack_start(row, False, False, 0)

        self._container.show_all()

    def _mouse_enter(self, *_args) -> bool:
        if self._on_pointer_enter:
            self._on_pointer_enter()
        return False

    def _mouse_leave(self, *_args) -> bool:
        if self._on_pointer_leave:
            self._on_pointer_leave()
        return False

    def pointer_is_inside(self) -> bool:
        if not self.get_visible():
            return False

        window = self.get_window()
        if window is None:
            return False

        pointer = self.get_display().get_default_seat().get_pointer()
        if pointer is None:
            return False

        _, root_x, root_y = pointer.get_position()
        origin = window.get_origin()
        if len(origin) == 3:
            _, widget_x, widget_y = origin
        else:
            widget_x, widget_y = origin

        allocation = self.get_allocation()
        return (
            widget_x <= root_x <= widget_x + allocation.width
            and widget_y <= root_y <= widget_y + allocation.height
        )
