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
from ...servicios.multimedia.media import (
    MEDIA_CHANGED,
    MEDIA_DISPLAY_MODE_CHANGED,
    MEDIA_DISPLAY_PLAYER,
    MediaService,
    is_strawberry_player,
)
from ..multimedia.audio_spectrum import paint_spectrum
from ..multimedia.media_format import (
    compact_bar_primary,
    compact_bar_secondary,
    window_bar_primary,
    window_bar_secondary,
)

ACTIVE_WINDOW_CHANGED = "active_window_changed"
MEDIA_BAR_CLICKED = "media_bar_clicked"


class ActiveWindowWidget(Gtk.EventBox):
    """Bar cava block: left opens the modes popup; right transport always drives Reproductor."""

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
        self.set_above_child(False)
        self.set_visible_window(True)
        self.connect("draw", self._on_draw_spectrum)

        # Same cava chrome, two non-overlapping hit targets:
        #   [ open_zone → popup ] [ transport → Strawberry only ]
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        body.set_hexpand(True)
        body.get_style_context().add_class("active-window-body")

        self._open_zone = Gtk.EventBox()
        self._open_zone.get_style_context().add_class("active-window-open-zone")
        self._open_zone.set_hexpand(True)
        self._open_zone.set_above_child(False)
        self._open_zone.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._open_zone.connect("button-press-event", self._on_open_zone_press)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        meta.set_hexpand(True)
        meta.get_style_context().add_class("active-window-meta")

        header = Gtk.Box(spacing=ACTIVE_WINDOW_CONTENT_SPACING)
        header.get_style_context().add_class("active-window-header")
        header.set_hexpand(True)

        self._icon = Gtk.Image()
        self._icon.get_style_context().add_class("active-window-icon")
        self._primary = Gtk.Label()
        self._primary.get_style_context().add_class("active-window-application")
        self._configure_stable_label(self._primary)

        # Ventana-only playback glyph (hidden while transport is present).
        self._status = Gtk.Label()
        self._status.get_style_context().add_class("active-window-status")
        self._status.set_no_show_all(True)
        self._status.hide()
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

        meta.pack_start(header, False, False, 0)
        meta.pack_start(self._secondary, False, False, 0)
        self._open_zone.add(meta)

        # Always packed inside the bar block; always visible; always Strawberry APIs.
        self._transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._transport.get_style_context().add_class("active-window-transport")
        self._transport.set_valign(Gtk.Align.CENTER)
        self._transport.set_halign(Gtk.Align.END)

        self._prev_button = self._make_transport_button("⏮", "Anterior (Reproductor)")
        self._play_button = self._make_transport_button("▶", "Reproducir / pausar (Reproductor)")
        self._next_button = self._make_transport_button("⏭", "Siguiente (Reproductor)")
        self._prev_button.connect("clicked", self._on_previous_clicked)
        self._play_button.connect("clicked", self._on_play_pause_clicked)
        self._next_button.connect("clicked", self._on_next_clicked)
        self._transport.pack_start(self._prev_button, False, False, 0)
        self._transport.pack_start(self._play_button, False, False, 0)
        self._transport.pack_start(self._next_button, False, False, 0)

        body.pack_start(self._open_zone, True, True, 0)
        body.pack_end(self._transport, False, False, 0)
        self.add(body)

        self._event_bus.subscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window_changed)
        self._event_bus.subscribe(MEDIA_CHANGED, self._on_media_changed)
        self._event_bus.subscribe(MEDIA_DISPLAY_MODE_CHANGED, self._on_display_mode_changed)
        self._event_bus.subscribe(AUDIO_VISUALIZER_CHANGED, self._on_visualizer_changed)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._render)

    def get_anchor_widget(self) -> Gtk.Widget:
        return self

    def _make_transport_button(self, label: str, tooltip: str) -> Gtk.Button:
        """Text glyphs match the old status style so controls blend into the cava chrome."""
        button = Gtk.Button(label=label, relief=Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("active-window-transport-btn")
        button.set_tooltip_text(tooltip)
        button.set_can_focus(False)
        button.set_focus_on_click(False)
        return button

    def _on_previous_clicked(self, _button: Gtk.Button) -> None:
        self._media_service.previous_track_player()

    def _on_play_pause_clicked(self, _button: Gtk.Button) -> None:
        self._media_service.play_pause_player()

    def _on_next_clicked(self, _button: Gtk.Button) -> None:
        self._media_service.next_track_player()

    @staticmethod
    def _configure_stable_label(label: Gtk.Label) -> None:
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

    def _on_display_mode_changed(self, _mode: str) -> None:
        GLib.idle_add(self._render)

    def _showing_media(self) -> bool:
        if self._media_service.display_mode == MEDIA_DISPLAY_PLAYER:
            active = self._media_snapshot.active
            return active is not None and is_strawberry_player(active)
        return self._media_snapshot.has_media and self._media_snapshot.active is not None

    def _on_visualizer_changed(self, snapshot: AudioVisualizerSnapshot) -> None:
        GLib.idle_add(self._store_visualizer_snapshot, snapshot)

    def _store_visualizer_snapshot(self, snapshot: AudioVisualizerSnapshot) -> bool:
        self._visualizer_snapshot = snapshot
        self.queue_draw()
        return False

    def _sync_play_glyph(self) -> None:
        """Play glyph follows Strawberry status when present; otherwise idle ▶."""
        active = self._media_snapshot.active
        playing = (
            active is not None
            and is_strawberry_player(active)
            and active.status == "playing"
        )
        self._play_button.set_label("⏸" if playing else "▶")
        self._play_button.set_tooltip_text(
            "Pausar (Reproductor)" if playing else "Reproducir (Reproductor)"
        )

    def _render(self) -> bool:
        style = self.get_style_context()
        # Transport is always shown inside this bar object.
        self._transport.show_all()
        self._status.hide()

        if self._media_service.display_mode == MEDIA_DISPLAY_PLAYER:
            style.add_class("active-window-player-mode")
            active = self._media_snapshot.active
            if active is not None and is_strawberry_player(active):
                style.add_class("active-window-media")
                self._render_media(active)
            else:
                style.add_class("active-window-media")
                self._render_player_idle()
        else:
            style.remove_class("active-window-player-mode")
            if self._showing_media():
                style.add_class("active-window-media")
                self._render_media(self._media_snapshot.active)  # type: ignore[arg-type]
            else:
                style.remove_class("active-window-media")
                self._render_window(self._active_window)

        self._sync_play_glyph()
        self.queue_draw()
        self.show_all()
        self._transport.show_all()
        self._status.hide()
        return False

    def _on_draw_spectrum(self, widget: Gtk.EventBox, cr) -> bool:
        allocation = widget.get_allocation()
        width = max(1, allocation.width)
        height = max(1, allocation.height)
        style = widget.get_style_context()
        Gtk.render_background(style, cr, 0, 0, width, height)
        Gtk.render_frame(style, cr, 0, 0, width, height)

        snapshot = self._visualizer_snapshot
        if self._showing_media() and snapshot.visible and any(snapshot.bars):
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
        self._status.set_text("")
        secondary = compact_bar_secondary(player)
        self._secondary.set_text(secondary)
        self._secondary.set_no_show_all(not bool(secondary.strip()))

    def _render_player_idle(self) -> None:
        self._icon.set_from_icon_name("audio-x-generic-symbolic", Gtk.IconSize.MENU)
        self._icon.set_pixel_size(ACTIVE_WINDOW_ICON_SIZE)
        self._primary.set_text("Strawberry")
        self._status.set_text("")
        self._secondary.set_text("Sin reproducción")
        self._secondary.set_no_show_all(False)

    def _render_window(self, active_window: ActiveWindow) -> None:
        self._icon.set_from_icon_name(active_window.icon, Gtk.IconSize.MENU)
        self._icon.set_pixel_size(ACTIVE_WINDOW_ICON_SIZE)
        self._primary.set_text(window_bar_primary(active_window))
        self._status.set_text("")
        self._secondary.set_text(window_bar_secondary(active_window))
        self._secondary.set_no_show_all(False)

    def _on_open_zone_press(self, _widget: Gtk.EventBox, event: Gdk.EventButton) -> bool:
        if event.button != Gdk.BUTTON_PRIMARY:
            return False
        self._event_bus.emit(MEDIA_BAR_CLICKED, self)
        return True

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(ACTIVE_WINDOW_CHANGED, self._on_active_window_changed)
        self._event_bus.unsubscribe(MEDIA_CHANGED, self._on_media_changed)
        self._event_bus.unsubscribe(MEDIA_DISPLAY_MODE_CHANGED, self._on_display_mode_changed)
        self._event_bus.unsubscribe(AUDIO_VISUALIZER_CHANGED, self._on_visualizer_changed)
