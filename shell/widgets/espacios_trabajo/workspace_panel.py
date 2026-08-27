"""Panel de audio por workspace: ventana GTK anclada debajo del bloque."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, Gtk

from ...config import WORKSPACE_POPUP_OFFSET
from ...popup_handle import present_popup, hide_popup
from ...models import AudioSnapshot, WorkspaceAudioState
from ...window_identity import (
    TITLE_WORKSPACE_PANEL,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from ..audio.audio_stream_row import AudioStreamRow, group_streams_by_app


class WorkspacePanel(Gtk.Window):
    """Panel completo de audio (click derecho), como ventana GTK bajo el bloque."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        on_volume_change: Callable[[str, float], None] | None = None,
        on_mute_toggle: Callable[[str], None] | None = None,
        on_playback_device_change: Callable[[str, str], None] | None = None,
        on_capture_device_change: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self.workspace_id: int | None = None
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._on_volume_change = on_volume_change
        self._on_mute_toggle = on_mute_toggle
        self._on_playback_device_change = on_playback_device_change
        self._on_capture_device_change = on_capture_device_change
        self._rows: dict[str, AudioStreamRow] = {}
        self._output_devices: tuple = ()
        self._input_devices: tuple = ()

        self.set_name("shell-workspace-panel")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_WORKSPACE_PANEL)
        configure_interactive_popup(self)

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._box.get_style_context().add_class("workspace-panel-content")

        self._header = Gtk.Label(label="Audio del workspace", xalign=0)
        self._header.get_style_context().add_class("workspace-panel-header")
        self._box.pack_start(self._header, False, False, 0)

        self._streams_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._box.pack_start(self._streams_container, True, True, 0)

        self.add(self._box)

    def open_for(
        self,
        button: Gtk.Widget,
        workspace_id: int,
        audio_state: WorkspaceAudioState | None = None,
        snapshot: AudioSnapshot | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self._anchor_button = button
        self._fixed_popup_top = None
        self._header.set_text(f"Audio · workspace {workspace_id}")
        if snapshot is not None:
            self._output_devices = snapshot.output_devices
            self._input_devices = snapshot.input_devices
        self._render_audio_state(audio_state)
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._anchor_button is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor_button,
                title=TITLE_WORKSPACE_PANEL,
                offset=WORKSPACE_POPUP_OFFSET,
                fixed_top=self._fixed_popup_top,
            )
            if self._fixed_popup_top is None and top is not None:
                self._fixed_popup_top = top
        return False

    def close_panel(self) -> None:
        self.workspace_id = None
        self._anchor_button = None
        self._fixed_popup_top = None
        hide_popup(self)

    def toggle_for(
        self,
        button: Gtk.Widget,
        workspace_id: int,
        audio_state: WorkspaceAudioState | None = None,
        snapshot: AudioSnapshot | None = None,
    ) -> None:
        if self.get_visible() and self.workspace_id == workspace_id:
            self.close_panel()
        else:
            self.open_for(button, workspace_id, audio_state, snapshot)

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

    def _render_audio_state(self, audio_state: WorkspaceAudioState | None) -> None:
        streams = audio_state.streams if audio_state else ()
        groups = group_streams_by_app(streams)
        active_keys = {row_key for row_key, _playback, _capture in groups}

        for row_key, row in list(self._rows.items()):
            if row_key not in active_keys:
                self._streams_container.remove(row)
                del self._rows[row_key]

        for child in self._streams_container.get_children():
            if not isinstance(child, AudioStreamRow):
                self._streams_container.remove(child)

        if not groups:
            empty_lbl = Gtk.Label(label="Sin audio activo en este workspace", xalign=0)
            empty_lbl.get_style_context().add_class("workspace-panel-empty")
            self._streams_container.pack_start(empty_lbl, False, False, 0)
            self._streams_container.show_all()
            return

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
                self._streams_container.pack_start(row, False, False, 0)

        self._streams_container.show_all()

    def is_adjusting_volume(self) -> bool:
        return any(row.is_dragging for row in self._rows.values())

    def sync_audio(
        self,
        audio_state: WorkspaceAudioState | None,
        snapshot: AudioSnapshot | None = None,
    ) -> None:
        if self.workspace_id is None or not self.get_visible() or self._anchor_button is None:
            return
        if self.is_adjusting_volume():
            return
        if snapshot is not None:
            self._output_devices = snapshot.output_devices
            self._input_devices = snapshot.input_devices
        self._render_audio_state(audio_state)
        schedule_popup_position(self._position_after_show)
