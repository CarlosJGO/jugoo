"""Shared row widget for workspace playback/capture streams."""

from __future__ import annotations

from typing import Callable, Iterable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...config import APPLICATION_ICON_SIZE
from ...models import AudioDevice, AudioStream
from .volume_slider import VolumeSlider


def group_streams_by_app(streams: Iterable[AudioStream]) -> list[tuple[str, AudioStream | None, AudioStream | None]]:
    """Group playback/capture streams that belong to the same application window."""
    groups: dict[str, dict[str, AudioStream | None]] = {}
    for stream in streams:
        key = stream.window_address or f"{stream.application_name}:{stream.workspace_id}"
        entry = groups.setdefault(key, {"playback": None, "capture": None})
        entry[stream.stream_kind] = stream
    return [(key, entry["playback"], entry["capture"]) for key, entry in groups.items()]


class _StreamControls(Gtk.Box):
    """Volume, mute, current device label, and device selector for one stream role."""

    def __init__(
        self,
        stream: AudioStream,
        devices: tuple[AudioDevice, ...],
        *,
        role_label: str,
        on_volume_change: Callable[[str, float], None] | None,
        on_mute_toggle: Callable[[str], None] | None,
        on_device_change: Callable[[str, str], None] | None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.stream_id = stream.id
        self._on_device_change = on_device_change
        self._updating_device = False
        self.get_style_context().add_class("audio-stream-role")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        role = Gtk.Label(label=role_label, xalign=0)
        role.get_style_context().add_class("audio-stream-role-label")
        header.pack_start(role, False, False, 0)

        mute_btn = Gtk.Button()
        mute_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._mute_img = Gtk.Image.new_from_icon_name(
            "audio-volume-muted" if stream.is_muted else "audio-volume-high",
            Gtk.IconSize.MENU,
        )
        mute_btn.add(self._mute_img)
        if on_mute_toggle:
            mute_btn.connect("clicked", lambda _b: on_mute_toggle(stream.id))
        header.pack_end(mute_btn, False, False, 0)
        self.pack_start(header, False, False, 0)

        slider_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.slider = VolumeSlider(
            initial_volume=stream.volume,
            on_change=lambda value: (
                on_volume_change(stream.id, value) if on_volume_change else None
            ),
        )
        slider_row.pack_start(self.slider, True, True, 0)
        self.pack_start(slider_row, False, False, 0)

        device_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        device_label = Gtk.Label(label=f"{stream.device_name}", xalign=0)
        device_label.get_style_context().add_class("audio-stream-device-current")
        device_label.set_hexpand(True)
        device_row.pack_start(device_label, True, True, 0)
        self._device_label = device_label

        self._device_combo = Gtk.ComboBoxText()
        self._device_ids: list[str] = []
        for device in devices:
            self._device_combo.append(device.id, device.description)
            self._device_ids.append(device.id)
        if stream.device_id:
            self._device_combo.set_active_id(stream.device_id)
        if on_device_change:
            self._device_combo.connect("changed", self._on_device_combo_changed)
        self._device_combo.set_sensitive(bool(devices))
        device_row.pack_start(self._device_combo, False, False, 0)
        self.pack_start(device_row, False, False, 0)

    def _on_device_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._updating_device or self._on_device_change is None:
            return
        device_id = combo.get_active_id()
        if device_id:
            self._on_device_change(self.stream_id, device_id)

    def _sync_device_combo(
        self,
        devices: tuple[AudioDevice, ...],
        active_id: str,
    ) -> None:
        new_ids = [device.id for device in devices]
        if new_ids != self._device_ids:
            self._updating_device = True
            self._device_combo.remove_all()
            self._device_ids = []
            for device in devices:
                self._device_combo.append(device.id, device.description)
                self._device_ids.append(device.id)
            self._updating_device = False
        if active_id and active_id in self._device_ids:
            if self._device_combo.get_active_id() != active_id:
                self._updating_device = True
                self._device_combo.set_active_id(active_id)
                self._updating_device = False
        self._device_combo.set_sensitive(bool(devices))

    def update_stream(self, stream: AudioStream, devices: tuple[AudioDevice, ...]) -> None:
        self.stream_id = stream.id
        self._mute_img.set_from_icon_name(
            "audio-volume-muted" if stream.is_muted else "audio-volume-high",
            Gtk.IconSize.MENU,
        )
        if not self.slider.is_dragging:
            self.slider.set_volume(stream.volume)
        self._device_label.set_text(stream.device_name)
        self._sync_device_combo(devices, stream.device_id)

    @property
    def is_dragging(self) -> bool:
        return self.slider.is_dragging


class AudioStreamRow(Gtk.Box):
    """One application row with optional playback and capture controls."""

    def __init__(
        self,
        row_key: str,
        playback: AudioStream | None,
        capture: AudioStream | None,
        output_devices: tuple[AudioDevice, ...],
        input_devices: tuple[AudioDevice, ...],
        *,
        on_volume_change: Callable[[str, float], None] | None = None,
        on_mute_toggle: Callable[[str], None] | None = None,
        on_playback_device_change: Callable[[str, str], None] | None = None,
        on_capture_device_change: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.row_key = row_key
        self.get_style_context().add_class("audio-stream-row")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_stream = playback or capture
        assert icon_stream is not None
        icon = Gtk.Image.new_from_icon_name(
            icon_stream.icon or "audio-volume-high",
            Gtk.IconSize.MENU,
        )
        icon.set_pixel_size(APPLICATION_ICON_SIZE)
        header.pack_start(icon, False, False, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label=icon_stream.application_name, xalign=0)
        title.get_style_context().add_class("audio-stream-app-name")
        text_box.pack_start(title, False, False, 0)
        if icon_stream.title and icon_stream.title != icon_stream.application_name:
            subtitle = Gtk.Label(label=icon_stream.title, xalign=0)
            subtitle.get_style_context().add_class("audio-stream-media-title")
            subtitle.set_max_width_chars(24)
            text_box.pack_start(subtitle, False, False, 0)
        header.pack_start(text_box, True, True, 0)
        self.pack_start(header, False, False, 0)

        self._playback_controls: _StreamControls | None = None
        self._capture_controls: _StreamControls | None = None

        if playback is not None:
            self._playback_controls = _StreamControls(
                playback,
                output_devices,
                role_label="Salida",
                on_volume_change=on_volume_change,
                on_mute_toggle=on_mute_toggle,
                on_device_change=on_playback_device_change,
            )
            self.pack_start(self._playback_controls, False, False, 0)

        if capture is not None:
            self._capture_controls = _StreamControls(
                capture,
                input_devices,
                role_label="Entrada",
                on_volume_change=on_volume_change,
                on_mute_toggle=on_mute_toggle,
                on_device_change=on_capture_device_change,
            )
            self.pack_start(self._capture_controls, False, False, 0)

    def update_group(
        self,
        playback: AudioStream | None,
        capture: AudioStream | None,
        output_devices: tuple[AudioDevice, ...],
        input_devices: tuple[AudioDevice, ...],
    ) -> None:
        if self._playback_controls is not None and playback is not None:
            if not self._playback_controls.is_dragging:
                self._playback_controls.update_stream(playback, output_devices)
        if self._capture_controls is not None and capture is not None:
            if not self._capture_controls.is_dragging:
                self._capture_controls.update_stream(capture, input_devices)

    @property
    def is_dragging(self) -> bool:
        dragging = False
        if self._playback_controls is not None:
            dragging = dragging or self._playback_controls.is_dragging
        if self._capture_controls is not None:
            dragging = dragging or self._capture_controls.is_dragging
        return dragging
