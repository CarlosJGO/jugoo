"""Visual module for the active-window state published by HyprlandService."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ...config import (
    ACTIVE_WINDOW_CONTENT_SPACING,
    ACTIVE_WINDOW_ICON_SIZE,
    ACTIVE_WINDOW_WIDTH,
    AUDIO_VISUALIZER_BAR_COUNT,
)
from ...eventbus import EventBus
from ...models import ActiveWindow, AudioVisualizerSnapshot, MediaSnapshot
from ...servicios.audio.audio_visualizer import AUDIO_VISUALIZER_CHANGED
from ...servicios.multimedia.media import MEDIA_CHANGED, MediaService
from ..multimedia.audio_spectrum import paint_spectrum
from ..multimedia.media_format import (
    compact_bar_primary,
    compact_bar_secondary,
    media_status_glyph,
    media_status_label,
    window_bar_primary,
    window_bar_secondary,
)

ACTIVE_WINDOW_CHANGED = "active_window_changed"
MEDIA_BAR_CLICKED = "media_bar_clicked"


class ActiveWindowWidget(Gtk.EventBox):
    """Draws active-window or compact MPRIS metadata; left click opens the media popup."""

    def __init__(
        self,
        event_bus: EventBus,
        media_service: MediaService,
    ) -> None:
        super().__init__()
        self._event_bus = event_bus
        self._media_service = media_service
        self._active_window = ActiveWindow(
            address="",
            app_class="",
            application_name="",
            title="",
            icon="window-new-symbolic",
        )
        self._media_snapshot = media_service.snapshot
        self._visualizer_snapshot = AudioVisualizerSnapshot.hidden(AUDIO_VISUALIZER_BAR_COUNT)

        self.get_style_context().add_class("active-window-widget")
        self.set_size_request(ACTIVE_WINDOW_WIDTH, -1)
        self.set_app_paintable(True)
        self.connect("button-press-event", self._on_button_press)
        self.connect("draw", self._on_draw_spectrum)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_hexpand(True)

        header = Gtk.Box(spacing=ACTIVE_WINDOW_CONTENT_SPACING)
        header.get_style_context().add_class("active-window-header")
        header.set_hexpand(True)

        self._icon = Gtk.Image()
        self._icon.get_style_context().add_class("active-window-icon")
        self._primary = Gtk.Label()
        self._primary.get_style_context().add_class("active-window-application")
        self._configure_stable_label(self._primary)

        self._status = Gtk.Label()
        self._status.get_style_context().add_class("active-window-status")
        self._status.set_xalign(1.0)
        self._status.set_halign(Gtk.Align.END)
        self._status.set_ellipsize(Pango.EllipsizeMode.NONE)
        self._status.set_single_line_mode(True)

        header.pack_start(self._icon, False, False, 0)
        header.pack_start(self._primary, True, True, 0)
        header.pack_end(self._status, False, False, 0)

        self._secondary = Gtk.Label()
        self._configure_stable_label(self._secondary)
        self._secondary.get_style_context().add_class("active-window-title")

        content.pack_start(header, False, False, 0)
        content.pack_start(self._secondary, False, False, 0)
        self.add(content)

        self._event_bus.subscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window_changed)
        self._event_bus.subscribe(MEDIA_CHANGED, self._on_media_changed)
        self._event_bus.subscribe(AUDIO_VISUALIZER_CHANGED, self._on_visualizer_changed)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._render)

    def get_anchor_widget(self) -> Gtk.Widget:
        return self

    @staticmethod
    def _configure_stable_label(label: Gtk.Label) -> None:
        """Keep label text left-aligned and ellipsized inside the fixed-width block."""
        label.set_xalign(0.0)
        label.set_halign(Gtk.Align.FILL)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_single_line_mode(True)
        label.set_max_width_chars(0)

    def _on_active_window_changed(self, active_window: ActiveWindow) -> None:
        GLib.idle_add(self._store_active_window, active_window)

    def _store_active_window(self, active_window: ActiveWindow) -> bool:
        self._active_window = active_window
        self._render()
        return False

    def _on_media_changed(self, snapshot: MediaSnapshot) -> None:
        self._store_media_snapshot(snapshot)

    def _store_media_snapshot(self, snapshot: MediaSnapshot) -> bool:
        self._media_snapshot = snapshot
        self._render()
        return False

    def _on_visualizer_changed(self, snapshot: AudioVisualizerSnapshot) -> None:
        GLib.idle_add(self._store_visualizer_snapshot, snapshot)

    def _store_visualizer_snapshot(self, snapshot: AudioVisualizerSnapshot) -> bool:
        self._visualizer_snapshot = snapshot
        self.queue_draw()
        return False

    def _render(self) -> bool:
        showing_media = self._media_snapshot.has_media and self._media_snapshot.active is not None
        style = self.get_style_context()
        if showing_media:
            style.add_class("active-window-media")
        else:
            style.remove_class("active-window-media")
        if showing_media:
            self._render_media(self._media_snapshot.active)  # type: ignore[arg-type]
        else:
            self._render_window(self._active_window)
        self.queue_draw()
        self.show_all()
        return False

    def _on_draw_spectrum(self, widget: Gtk.EventBox, cr) -> bool:
        """Paint CSS background + spectrum under the text child."""
        allocation = widget.get_allocation()
        width = max(1, allocation.width)
        height = max(1, allocation.height)
        style = widget.get_style_context()
        Gtk.render_background(style, cr, 0, 0, width, height)
        Gtk.render_frame(style, cr, 0, 0, width, height)

        snapshot = self._visualizer_snapshot
        showing_media = self._media_snapshot.has_media and self._media_snapshot.active is not None
        if showing_media and snapshot.visible and any(snapshot.bars):
            paint_spectrum(
                cr,
                width=width,
                height=height,
                bars=snapshot.bars,
                colors=snapshot.colors,
                peaks=snapshot.peaks,
            )
        return False

    def _render_media(self, player) -> None:
        icon_name = (
            "media-playback-pause-symbolic"
            if player.status == "paused"
            else "audio-x-generic-symbolic"
        )
        self._icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)
        self._icon.set_pixel_size(ACTIVE_WINDOW_ICON_SIZE)
        self._primary.set_text(compact_bar_primary(player))
        glyph = media_status_glyph(player.status)
        self._status.set_text(glyph)
        self._status.set_tooltip_text(media_status_label(player.status))
        secondary = compact_bar_secondary(player)
        self._secondary.set_text(secondary)
        self._secondary.set_no_show_all(not bool(secondary.strip()))

    def _render_window(self, active_window: ActiveWindow) -> None:
        self._icon.set_from_icon_name(active_window.icon, Gtk.IconSize.MENU)
        self._icon.set_pixel_size(ACTIVE_WINDOW_ICON_SIZE)
        self._primary.set_text(window_bar_primary(active_window))
        self._status.set_text("")
        self._status.set_tooltip_text(None)
        self._secondary.set_text(window_bar_secondary(active_window))
        self._secondary.set_no_show_all(False)

    def _on_button_press(self, _widget: Gtk.EventBox, event: Gdk.EventButton) -> bool:
        if event.button != Gdk.BUTTON_PRIMARY:
            return False
        if self._media_snapshot.has_media:
            self._event_bus.emit(MEDIA_BAR_CLICKED, self)
            return True
        return False

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window_changed)
        self._event_bus.unsubscribe(MEDIA_CHANGED, self._on_media_changed)
        self._event_bus.unsubscribe(AUDIO_VISUALIZER_CHANGED, self._on_visualizer_changed)
