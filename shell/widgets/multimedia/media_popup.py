"""Large media player popup anchored below the active-window block."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from ...config import MEDIA_POPUP_OFFSET
from ...models import MediaPlayerSnapshot, MediaSnapshot
from ...popup_handle import pointer_inside_widget, present_popup, hide_popup
from ...servicios.multimedia.media import MEDIA_AUTO_PLAYER_ID, MediaService
from ...window_identity import (
    TITLE_MEDIA_POPUP,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from .media_format import format_media_time_usec, media_status_label
from .media_popup_layout import media_popup_dimensions, scale_artwork_pixbuf


class MediaPopup(Gtk.Window):
    """Transport controls and metadata for the active MPRIS player."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        media_service: MediaService,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        popup_width, popup_max_height, artwork_size = media_popup_dimensions()
        self._popup_width = popup_width
        self._popup_max_height = popup_max_height
        self._artwork_size = artwork_size

        self._shell_window = shell_window
        self._service = media_service
        self._anchor: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._seek_dragging = False
        self._updating_progress = False
        self._artwork_key = ""

        self.set_name("shell-media-popup")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_MEDIA_POPUP)
        configure_interactive_popup(self)
        self.set_default_size(popup_width, popup_max_height)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.get_style_context().add_class("media-popup-content")
        outer.set_size_request(popup_width, -1)
        self.add(outer)

        self._player_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        player_label = Gtk.Label(label="Reproductor", xalign=0)
        player_label.get_style_context().add_class("media-popup-section-label")
        self._player_row.pack_start(player_label, False, False, 0)
        self._player_combo = Gtk.ComboBoxText()
        self._player_combo.connect("changed", self._on_player_combo_changed)
        self._player_row.pack_start(self._player_combo, True, True, 0)
        outer.pack_start(self._player_row, False, False, 0)

        self._body_scrolled = Gtk.ScrolledWindow()
        self._body_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self._body_scrolled.set_max_content_height(popup_max_height - 140)
        outer.pack_start(self._body_scrolled, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self._body_scrolled.add(body)

        artwork_frame = Gtk.Box()
        artwork_frame.set_size_request(artwork_size, artwork_size)
        artwork_frame.get_style_context().add_class("media-popup-artwork-frame")
        artwork_frame.set_halign(Gtk.Align.CENTER)
        artwork_frame.set_valign(Gtk.Align.START)
        self._artwork = Gtk.Image.new_from_icon_name(
            "audio-x-generic-symbolic",
            Gtk.IconSize.DIALOG,
        )
        self._artwork.get_style_context().add_class("media-popup-artwork")
        artwork_frame.pack_start(self._artwork, True, True, 0)
        body.pack_start(artwork_frame, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        meta.set_hexpand(True)
        meta.set_valign(Gtk.Align.CENTER)
        body.pack_start(meta, True, True, 0)

        self._title = self._ellipsized_label("media-popup-title")
        meta.pack_start(self._title, False, False, 0)

        self._artist = self._ellipsized_label("media-popup-detail")
        meta.pack_start(self._artist, False, False, 0)

        self._album = self._ellipsized_label("media-popup-detail-muted")
        meta.pack_start(self._album, False, False, 0)

        self._status = self._ellipsized_label("media-popup-status")
        meta.pack_start(self._status, False, False, 0)

        self._progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.pack_start(self._progress_box, False, False, 0)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._elapsed = Gtk.Label(label="0:00", xalign=0)
        self._elapsed.get_style_context().add_class("media-popup-time")
        self._duration = Gtk.Label(label="0:00", xalign=1)
        self._duration.get_style_context().add_class("media-popup-time")
        self._duration.set_hexpand(True)
        times.pack_start(self._elapsed, False, False, 0)
        times.pack_end(self._duration, False, False, 0)
        self._progress_box.pack_start(times, False, False, 0)

        self._progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.001)
        self._progress.set_draw_value(False)
        self._progress.set_hexpand(True)
        self._progress.get_style_context().add_class("media-popup-progress")
        self._progress.connect("button-press-event", self._on_progress_press)
        self._progress.connect("button-release-event", self._on_progress_release)
        self._progress.connect("change-value", self._on_progress_change)
        self._progress_box.pack_start(self._progress, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_halign(Gtk.Align.CENTER)
        outer.pack_start(controls, False, False, 0)

        self._prev_button = self._transport_button(
            "media-skip-backward-symbolic",
            self._service.previous_track,
        )
        self._play_button = self._transport_button(
            "media-playback-start-symbolic",
            self._service.play_pause,
        )
        self._next_button = self._transport_button(
            "media-skip-forward-symbolic",
            self._service.next_track,
        )
        controls.pack_start(self._prev_button, False, False, 0)
        controls.pack_start(self._play_button, False, False, 0)
        controls.pack_start(self._next_button, False, False, 0)

    @staticmethod
    def _ellipsized_label(css_class: str) -> Gtk.Label:
        label = Gtk.Label(xalign=0)
        label.get_style_context().add_class(css_class)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_single_line_mode(True)
        label.set_max_width_chars(0)
        return label

    def open_for(self, anchor: Gtk.Widget) -> None:
        self._anchor = anchor
        self._fixed_popup_top = None
        self.refresh(self._service.snapshot)
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor = None
        self._fixed_popup_top = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self, snapshot: MediaSnapshot) -> None:
        player = snapshot.active
        if player is None:
            return

        self._sync_player_combo(snapshot)
        self._title.set_markup(
            f"<b>{_escape_markup(player.title or player.identity)}</b>"
        )
        self._artist.set_text(player.artist or "")
        self._artist.set_no_show_all(not bool(player.artist))
        if player.artist:
            self._artist.show()
        else:
            self._artist.hide()
        self._album.set_text(player.album or "")
        self._album.set_no_show_all(not bool(player.album))
        if player.album:
            self._album.show()
        else:
            self._album.hide()
        self._status.set_text(media_status_label(player.status))
        self._apply_artwork(player)
        self._sync_progress(player)
        self._sync_controls(player)

        several_players = len(snapshot.players) > 1
        self._player_row.set_no_show_all(not several_players)
        if several_players:
            self._player_row.show()
        else:
            self._player_row.hide()

    def _transport_button(self, icon_name: str, callback: Callable[[], None]) -> Gtk.Button:
        button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("media-popup-control")
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        button.add(image)
        button.connect("clicked", lambda _btn: callback())
        return button

    def _sync_player_combo(self, snapshot: MediaSnapshot) -> None:
        player_ids = [player.bus_name for player in snapshot.players]
        combo_ids = [MEDIA_AUTO_PLAYER_ID, *player_ids]
        if combo_ids != getattr(self, "_combo_ids", None):
            self._player_combo.handler_block_by_func(self._on_player_combo_changed)
            self._player_combo.remove_all()
            self._combo_ids = combo_ids
            self._player_combo.append(MEDIA_AUTO_PLAYER_ID, "Automático")
            for player in snapshot.players:
                label = player.identity or player.bus_name.removeprefix("org.mpris.MediaPlayer2.")
                self._player_combo.append(player.bus_name, label)
            self._player_combo.handler_unblock_by_func(self._on_player_combo_changed)

        active_id = (
            self._service.manual_player
            if self._service.manual_player
            else MEDIA_AUTO_PLAYER_ID
        )
        self._player_combo.handler_block_by_func(self._on_player_combo_changed)
        self._player_combo.set_active_id(active_id)
        self._player_combo.handler_unblock_by_func(self._on_player_combo_changed)

    def _apply_artwork(self, player: MediaPlayerSnapshot) -> None:
        path = player.artwork_path.strip()
        key = f"{player.bus_name}:{path or 'icon:' + player.status}"
        if key == self._artwork_key:
            return
        self._artwork_key = key
        if path and Path(path).is_file():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
                self._artwork.set_from_pixbuf(
                    scale_artwork_pixbuf(pixbuf, self._artwork_size)
                )
                return
            except GLib.Error:
                self._artwork_key = ""
        icon = (
            "media-playback-pause-symbolic"
            if player.status == "paused"
            else "audio-x-generic-symbolic"
        )
        self._artwork.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
        self._artwork.set_pixel_size(self._artwork_size)

    def _sync_progress(self, player: MediaPlayerSnapshot) -> None:
        has_progress = player.duration_usec > 0
        self._progress_box.set_no_show_all(not has_progress)
        if has_progress:
            self._progress_box.show()
        else:
            self._progress_box.hide()
        if not has_progress or self._seek_dragging:
            return

        ratio = min(1.0, max(0.0, player.position_usec / player.duration_usec))
        self._updating_progress = True
        self._progress.set_value(ratio)
        self._updating_progress = False
        self._elapsed.set_text(format_media_time_usec(player.position_usec))
        self._duration.set_text(format_media_time_usec(player.duration_usec))
        self._progress.set_sensitive(player.can_seek)

    def _sync_controls(self, player: MediaPlayerSnapshot) -> None:
        self._prev_button.set_sensitive(player.can_go_previous)
        self._prev_button.set_visible(player.can_go_previous)
        self._next_button.set_sensitive(player.can_go_next)
        self._next_button.set_visible(player.can_go_next)
        self._play_button.set_sensitive(player.can_play or player.can_pause)
        play_icon = (
            self._play_button.get_children()[0]
            if self._play_button.get_children()
            else None
        )
        if isinstance(play_icon, Gtk.Image):
            icon_name = (
                "media-playback-pause-symbolic"
                if player.status == "playing"
                else "media-playback-start-symbolic"
            )
            play_icon.set_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        self._progress.set_sensitive(player.can_seek and player.duration_usec > 0)

    def _on_player_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        bus_name = combo.get_active_id()
        if bus_name == MEDIA_AUTO_PLAYER_ID:
            self._service.set_auto_player_selection()
        elif bus_name:
            self._service.set_active_player(str(bus_name))

    def _on_progress_press(self, _scale: Gtk.Scale, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._seek_dragging = True
        return False

    def _on_progress_release(self, _scale: Gtk.Scale, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._seek_dragging = False
            self._commit_seek(_scale.get_value())
        return False

    def _on_progress_change(
        self,
        scale: Gtk.Scale,
        _scroll: Gtk.ScrollType,
        value: float,
    ) -> bool:
        if self._updating_progress:
            return False
        player = self._service.snapshot.active
        if player is None or not player.can_seek or player.duration_usec <= 0:
            return True
        if self._seek_dragging:
            target = int(player.duration_usec * max(0.0, min(1.0, value)))
            self._elapsed.set_text(format_media_time_usec(target))
            return False
        self._commit_seek(value)
        return False

    def _commit_seek(self, ratio: float) -> None:
        player = self._service.snapshot.active
        if player is None or not player.can_seek or player.duration_usec <= 0:
            return
        target = int(player.duration_usec * max(0.0, min(1.0, ratio)))
        self._service.seek_to(target)

    def _position_after_show(self) -> bool:
        if self._anchor is None:
            return False
        self._fixed_popup_top = position_popup_below_anchor(
            self,
            self._anchor,
            title=TITLE_MEDIA_POPUP,
            offset=MEDIA_POPUP_OFFSET,
            fixed_top=self._fixed_popup_top,
        )
        return False


def _escape_markup(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
