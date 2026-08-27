"""Informational volume OSD for the default PipeWire/Pulse sink."""

from __future__ import annotations

import cairo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gtk, GtkLayerShell, Pango

from ...models import SystemVolumeState
from ...window_identity import (
    TITLE_VOLUME_OSD,
    configure_osd_window,
    configure_toplevel,
    register_shell_popup,
)

_BAR_SEGMENTS = 20


def volume_osd_glyph(state: SystemVolumeState) -> str:
    if state.is_muted or state.volume <= 0.001:
        return "🔇"
    if state.volume < 0.34:
        return "🔈"
    if state.volume < 0.67:
        return "🔉"
    return "🔊"


def volume_osd_headline(state: SystemVolumeState) -> str:
    if state.is_muted:
        return f"{volume_osd_glyph(state)} Silenciado"
    return f"{volume_osd_glyph(state)} {state.percent}%"


def volume_osd_bar_text(state: SystemVolumeState) -> str:
    filled = 0 if state.is_muted else int(round(state.percent / 100 * _BAR_SEGMENTS))
    filled = max(0, min(_BAR_SEGMENTS, filled))
    return ("█" * filled) + ("░" * (_BAR_SEGMENTS - filled))


def volume_osd_bar_fraction(state: SystemVolumeState) -> float:
    if state.is_muted:
        return 0.0
    return max(0.0, min(1.0, state.volume))


class VolumeOsd(Gtk.Window):
    """Single reusable OSD window. Never accepts focus, keyboard, or pointer."""

    def __init__(self, shell_window: Gtk.Window) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._shell_window = shell_window
        self._pass_through_applied = False

        self.set_name("shell-volume-osd")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_VOLUME_OSD)
        configure_osd_window(self)
        self._configure_layer_shell()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_valign(Gtk.Align.END)
        self.add(outer)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("volume-osd-content")
        card.set_halign(Gtk.Align.CENTER)
        outer.pack_start(card, False, False, 0)

        self._headline = Gtk.Label(xalign=0.5)
        self._headline.get_style_context().add_class("volume-osd-headline")
        card.pack_start(self._headline, False, False, 0)

        self._bar = Gtk.ProgressBar()
        self._bar.set_fraction(0.0)
        self._bar.set_show_text(False)
        self._bar.get_style_context().add_class("volume-osd-bar")
        card.pack_start(self._bar, False, False, 0)

        self._bar_text = Gtk.Label(xalign=0.5)
        self._bar_text.get_style_context().add_class("volume-osd-bar-text")
        card.pack_start(self._bar_text, False, False, 0)

        self._device = Gtk.Label(xalign=0.5)
        self._device.get_style_context().add_class("volume-osd-device")
        self._device.set_ellipsize(Pango.EllipsizeMode.END)
        card.pack_start(self._device, False, False, 0)

        self.connect("realize", self._on_realize)
        self.connect("map", self._on_map)
        self.connect("button-press-event", self._ignore_event)
        self.connect("button-release-event", self._ignore_event)
        self.connect("scroll-event", self._ignore_event)
        self.connect("key-press-event", self._ignore_event)
        self.connect("key-release-event", self._ignore_event)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-volume-osd")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        for edge in (
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
            GtkLayerShell.Edge.BOTTOM,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 96)

    def refresh(self, state: SystemVolumeState) -> None:
        self._headline.set_text(volume_osd_headline(state))
        self._bar.set_fraction(volume_osd_bar_fraction(state))
        self._bar_text.set_text(volume_osd_bar_text(state))
        self._device.set_text(state.sink_description)
        style = self.get_style_context()
        if state.is_muted:
            style.add_class("volume-osd-muted")
        else:
            style.remove_class("volume-osd-muted")

    def show_osd(self) -> None:
        """Show without activating or taking keyboard/pointer focus."""
        if not self.get_visible():
            self.set_opacity(1.0)
            self.show_all()
        self._apply_pass_through()

    def hide_osd(self) -> None:
        if self.get_visible():
            self.hide()

    def _on_realize(self, *_args) -> None:
        self._apply_pass_through()

    def _on_map(self, *_args) -> None:
        self._apply_pass_through()

    def _apply_pass_through(self) -> None:
        gdk_window = self.get_window()
        if gdk_window is None:
            return
        # Empty input region: pointer events pass through to the active app.
        region = cairo.Region()
        gdk_window.input_shape_combine_region(region, 0, 0)
        self._pass_through_applied = True

    @staticmethod
    def _ignore_event(*_args) -> bool:
        return True

    @property
    def accepts_focus(self) -> bool:
        return bool(self.get_accept_focus())

    @property
    def is_interactive(self) -> bool:
        return False
