"""Music-player popup anchored below the active-window block."""

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
from ...popup_handle import hide_popup, pointer_inside_widget, present_popup
from ...popup_spawn import publish_popup_spawn
from ...servicios.multimedia.media import (
    MEDIA_AUTO_PLAYER_ID,
    MEDIA_DISPLAY_PLAYER,
    MEDIA_DISPLAY_WINDOW,
    MediaService,
    is_strawberry_player,
    window_mode_players,
)
from ...ui.starfield import install_starfield, resolve_event_bus
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

_WINDOW_THUMB_SIZE = 72


class MediaPopup(Gtk.Window):
    """Vertical music-player chrome with bar mode switch and source picker."""

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
        # GTK ellipsize + max_width_chars(0) collapses labels to "…"; keep a real width.
        self._label_chars = max(18, (popup_width - 48) // 8)

        self._shell_window = shell_window
        self._service = media_service
        self._anchor: Gtk.Widget | None = None
        self._seek_dragging = False
        self._volume_dragging = False
        self._updating_progress = False
        self._updating_volume = False
        self._artwork_key = ""
        self._window_thumb_key = ""
        self._source_rows: dict[str, Gtk.ListBoxRow] = {}

        self.set_name("shell-media-popup")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_MEDIA_POPUP)
        configure_interactive_popup(self)
        self.set_default_size(popup_width, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.get_style_context().add_class("media-popup-content")
        outer.set_size_request(popup_width, -1)
        install_starfield(
            self,
            outer,
            resolve_event_bus(shell_window),
            corner_radius=16.0,
        )

        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        mode_row.get_style_context().add_class("media-popup-mode-switch")
        mode_row.set_halign(Gtk.Align.FILL)
        self._mode_window = Gtk.RadioButton.new_with_label(None, "Ventana")
        self._mode_window.set_mode(False)
        self._mode_window.get_style_context().add_class("media-popup-mode-btn")
        self._mode_player = Gtk.RadioButton.new_with_label_from_widget(
            self._mode_window,
            "Reproductor",
        )
        self._mode_player.set_mode(False)
        self._mode_player.get_style_context().add_class("media-popup-mode-btn")
        self._mode_window.set_hexpand(True)
        self._mode_player.set_hexpand(True)
        self._mode_window.connect("toggled", self._on_mode_window_toggled)
        self._mode_player.connect("toggled", self._on_mode_player_toggled)
        mode_row.pack_start(self._mode_window, True, True, 0)
        mode_row.pack_start(self._mode_player, True, True, 0)
        outer.pack_start(mode_row, False, False, 0)

        self._mode_stack = Gtk.Stack()
        self._mode_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._mode_stack.set_homogeneous(False)
        self._window_chrome = self._build_window_chrome()
        self._player_chrome = self._build_player_chrome(artwork_size)
        self._mode_stack.add_named(self._window_chrome, MEDIA_DISPLAY_WINDOW)
        self._mode_stack.add_named(self._player_chrome, MEDIA_DISPLAY_PLAYER)
        outer.pack_start(self._mode_stack, False, False, 0)
        self._mode_stack.set_visible_child_name(self._service.display_mode)

    def _build_window_chrome(self) -> Gtk.Box:
        chrome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        chrome.get_style_context().add_class("media-popup-window-chrome")

        source_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        source_row.get_style_context().add_class("media-popup-source-row")
        source_label = Gtk.Label(label="Fuente", xalign=0)
        source_label.get_style_context().add_class("media-popup-section-label")
        source_row.pack_start(source_label, False, False, 0)

        self._source_button = Gtk.MenuButton()
        self._source_button.get_style_context().add_class("media-popup-source-btn")
        self._source_button.set_hexpand(True)
        self._source_button.set_halign(Gtk.Align.FILL)
        self._source_button_label = Gtk.Label(label="Automático", xalign=0)
        self._source_button_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._source_button_label.set_max_width_chars(self._label_chars)
        self._source_button.add(self._source_button_label)
        self._source_popover = Gtk.Popover()
        self._source_popover.set_relative_to(self._source_button)
        self._source_list = Gtk.ListBox()
        self._source_list.get_style_context().add_class("media-popup-source-list")
        self._source_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._source_list.connect("row-activated", self._on_source_row_activated)
        self._source_popover.add(self._source_list)
        self._source_button.set_popover(self._source_popover)
        source_row.pack_start(self._source_button, True, True, 0)
        chrome.pack_start(source_row, False, False, 0)
        self._source_row = source_row

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.get_style_context().add_class("media-popup-window-card")
        chrome.pack_start(card, False, False, 0)

        thumb_frame = Gtk.Box()
        thumb_frame.set_size_request(_WINDOW_THUMB_SIZE, _WINDOW_THUMB_SIZE)
        thumb_frame.get_style_context().add_class("media-popup-window-thumb-frame")
        self._window_thumb = Gtk.Image.new_from_icon_name(
            "audio-x-generic-symbolic",
            Gtk.IconSize.DIALOG,
        )
        self._window_thumb.set_pixel_size(_WINDOW_THUMB_SIZE - 16)
        thumb_frame.pack_start(self._window_thumb, True, True, 0)
        card.pack_start(thumb_frame, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)
        card.pack_start(info, True, True, 0)

        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._window_kind = Gtk.Label(xalign=0)
        self._window_kind.get_style_context().add_class("media-popup-window-kind")
        self._window_status = Gtk.Label(xalign=0)
        self._window_status.get_style_context().add_class("media-popup-window-status")
        badges.pack_start(self._window_kind, False, False, 0)
        badges.pack_start(self._window_status, False, False, 0)
        info.pack_start(badges, False, False, 0)

        self._window_title = self._text_label("media-popup-window-title")
        info.pack_start(self._window_title, False, False, 0)
        self._window_detail = self._text_label("media-popup-window-detail")
        info.pack_start(self._window_detail, False, False, 0)

        self._window_play = self._transport_button(
            "media-playback-start-symbolic",
            self._service.play_pause,
            primary=True,
        )
        self._window_play.set_valign(Gtk.Align.CENTER)
        self._window_play.set_tooltip_text("Pausar / reanudar")
        card.pack_end(self._window_play, False, False, 0)

        self._window_progress_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        self._window_elapsed = Gtk.Label(label="0:00", xalign=0)
        self._window_elapsed.get_style_context().add_class("media-popup-time")
        self._window_progress = Gtk.ProgressBar()
        self._window_progress.set_hexpand(True)
        self._window_progress.set_fraction(0.0)
        self._window_progress.get_style_context().add_class("media-popup-window-progress")
        self._window_duration = Gtk.Label(label="0:00", xalign=1)
        self._window_duration.get_style_context().add_class("media-popup-time")
        self._window_progress_box.pack_start(self._window_elapsed, False, False, 0)
        self._window_progress_box.pack_start(self._window_progress, True, True, 0)
        self._window_progress_box.pack_start(self._window_duration, False, False, 0)
        chrome.pack_start(self._window_progress_box, False, False, 0)

        hint = Gtk.Label(
            label="El cava de la barra sigue esta fuente",
            xalign=0,
        )
        hint.get_style_context().add_class("media-popup-window-hint")
        hint.set_line_wrap(True)
        chrome.pack_start(hint, False, False, 0)
        return chrome

    def _build_player_chrome(self, artwork_size: int) -> Gtk.Box:
        chrome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        chrome.get_style_context().add_class("media-popup-player-chrome")

        artwork_frame = Gtk.Box()
        artwork_frame.set_size_request(artwork_size, artwork_size)
        artwork_frame.get_style_context().add_class("media-popup-artwork-frame")
        artwork_frame.set_halign(Gtk.Align.CENTER)
        self._artwork = Gtk.Image.new_from_icon_name(
            "audio-x-generic-symbolic",
            Gtk.IconSize.DIALOG,
        )
        self._artwork.get_style_context().add_class("media-popup-artwork")
        artwork_frame.pack_start(self._artwork, True, True, 0)
        chrome.pack_start(artwork_frame, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        meta.set_halign(Gtk.Align.FILL)
        meta.set_hexpand(True)
        chrome.pack_start(meta, False, False, 0)

        self._title = self._text_label("media-popup-title", center=True)
        meta.pack_start(self._title, False, False, 0)
        self._artist = self._text_label("media-popup-detail", center=True)
        meta.pack_start(self._artist, False, False, 0)
        self._album = self._text_label("media-popup-detail-muted", center=True)
        meta.pack_start(self._album, False, False, 0)

        self._progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        chrome.pack_start(self._progress_box, False, False, 0)

        self._progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.001)
        self._progress.set_draw_value(False)
        self._progress.set_hexpand(True)
        self._progress.get_style_context().add_class("media-popup-progress")
        self._progress.connect("button-press-event", self._on_progress_press)
        self._progress.connect("button-release-event", self._on_progress_release)
        self._progress.connect("change-value", self._on_progress_change)
        self._progress_box.pack_start(self._progress, False, False, 0)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._elapsed = Gtk.Label(label="0:00", xalign=0)
        self._elapsed.get_style_context().add_class("media-popup-time")
        self._duration = Gtk.Label(label="0:00", xalign=1)
        self._duration.get_style_context().add_class("media-popup-time")
        self._duration.set_hexpand(True)
        times.pack_start(self._elapsed, False, False, 0)
        times.pack_end(self._duration, False, False, 0)
        self._progress_box.pack_start(times, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls.set_halign(Gtk.Align.CENTER)
        controls.get_style_context().add_class("media-popup-transport")
        chrome.pack_start(controls, False, False, 0)

        self._prev_button = self._transport_button(
            "media-skip-backward-symbolic",
            self._service.previous_track,
        )
        self._play_button = self._transport_button(
            "media-playback-start-symbolic",
            self._service.play_pause,
            primary=True,
        )
        self._next_button = self._transport_button(
            "media-skip-forward-symbolic",
            self._service.next_track,
        )
        controls.pack_start(self._prev_button, False, False, 0)
        controls.pack_start(self._play_button, False, False, 0)
        controls.pack_start(self._next_button, False, False, 0)

        extras = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        extras.set_halign(Gtk.Align.FILL)
        extras.get_style_context().add_class("media-popup-extras")
        chrome.pack_start(extras, False, False, 0)

        self._loop_button = self._transport_button(
            "media-playlist-repeat-symbolic",
            self._service.cycle_loop,
        )
        self._loop_button.set_tooltip_text("Repetición")
        extras.pack_start(self._loop_button, False, False, 0)

        vol_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vol_box.set_hexpand(True)
        vol_icon = Gtk.Image.new_from_icon_name(
            "audio-volume-medium-symbolic",
            Gtk.IconSize.BUTTON,
        )
        vol_icon.get_style_context().add_class("media-popup-volume-icon")
        vol_box.pack_start(vol_icon, False, False, 0)
        self._volume_icon = vol_icon
        self._volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01)
        self._volume.set_draw_value(False)
        self._volume.set_hexpand(True)
        self._volume.get_style_context().add_class("media-popup-volume")
        self._volume.connect("button-press-event", self._on_volume_press)
        self._volume.connect("button-release-event", self._on_volume_release)
        self._volume.connect("value-changed", self._on_volume_changed)
        vol_box.pack_start(self._volume, True, True, 0)
        extras.pack_start(vol_box, True, True, 0)
        return chrome

    def _text_label(self, css_class: str, *, center: bool = False) -> Gtk.Label:
        label = Gtk.Label(xalign=0.5 if center else 0.0)
        label.get_style_context().add_class(css_class)
        label.set_halign(Gtk.Align.FILL)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_single_line_mode(True)
        label.set_max_width_chars(self._label_chars)
        label.set_width_chars(min(12, self._label_chars))
        if center:
            label.set_justify(Gtk.Justification.CENTER)
        return label

    def open_for(self, anchor: Gtk.Widget) -> None:
        self._anchor = anchor
        publish_popup_spawn(
            self,
            anchor,
            title=TITLE_MEDIA_POPUP,
            offset=MEDIA_POPUP_OFFSET,
        )
        present_popup(self)
        # After show_all(), reveal exactly one mode and sync its contents.
        self.refresh(self._service.snapshot)
        schedule_popup_position(self._position_after_show)

    def close_popup(self) -> None:
        self._anchor = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def _apply_mode_chrome(self) -> None:
        name = (
            MEDIA_DISPLAY_PLAYER
            if self._service.display_mode == MEDIA_DISPLAY_PLAYER
            else MEDIA_DISPLAY_WINDOW
        )
        if self._mode_stack.get_visible_child_name() != name:
            self._mode_stack.set_visible_child_name(name)

    def refresh(self, snapshot: MediaSnapshot) -> None:
        player = snapshot.active
        player_mode = self._service.display_mode == MEDIA_DISPLAY_PLAYER
        self._sync_mode_buttons()
        self._apply_mode_chrome()

        if player_mode:
            if player is not None and is_strawberry_player(player):
                self._refresh_player(player)
            else:
                self._refresh_player_idle()
        else:
            self._sync_source_picker(snapshot)
            if player is not None:
                self._refresh_window(player)
            else:
                self._refresh_window_idle()

        if self.get_visible() and self._anchor is not None:
            schedule_popup_position(self._position_after_show)

    def _refresh_player(self, player: MediaPlayerSnapshot) -> None:
        title = (player.title or player.identity or "Sin título").strip()
        artist = (player.artist or "").strip()
        album = (player.album or "").strip()
        self._title.set_text(title)
        # Keep lines always mapped so missing artist/album does not resize the popup.
        self._artist.set_text(artist if artist else " ")
        self._artist.set_visible(True)
        self._album.set_text(album if album else " ")
        self._album.set_visible(True)
        self._apply_artwork(self._artwork, player, self._artwork_size, "_artwork_key")
        self._sync_progress(player)
        self._sync_controls(player)
        self._sync_loop(player)
        self._sync_volume(player)

    def _refresh_player_idle(self) -> None:
        self._artwork_key = ""
        self._artwork.set_from_icon_name("audio-x-generic-symbolic", Gtk.IconSize.DIALOG)
        self._artwork.set_pixel_size(self._artwork_size)
        self._title.set_text("Strawberry")
        self._artist.set_text("No está en ejecución")
        self._artist.set_visible(True)
        self._album.set_text("Pulsa play para abrirlo")
        self._album.set_visible(True)
        self._progress_box.set_visible(False)
        self._prev_button.set_visible(False)
        self._next_button.set_visible(False)
        self._play_button.set_sensitive(True)
        play_icon = (
            self._play_button.get_children()[0]
            if self._play_button.get_children()
            else None
        )
        if isinstance(play_icon, Gtk.Image):
            play_icon.set_from_icon_name(
                "media-playback-start-symbolic",
                Gtk.IconSize.BUTTON,
            )
        self._loop_button.set_sensitive(False)
        self._volume.set_sensitive(False)

    def _refresh_window(self, player: MediaPlayerSnapshot) -> None:
        kind = _source_kind_label(player)
        self._window_kind.set_text(kind)
        self._window_status.set_text(media_status_label(player.status))
        title = (player.title or player.identity or "Sin título").strip()
        detail_parts = [part for part in (player.artist, player.album) if part.strip()]
        detail = " · ".join(detail_parts) if detail_parts else player.identity
        self._window_title.set_text(title)
        self._window_detail.set_text(detail)
        self._window_detail.set_visible(bool(detail.strip()))
        self._apply_artwork(
            self._window_thumb,
            player,
            _WINDOW_THUMB_SIZE,
            "_window_thumb_key",
        )
        can_toggle = player.can_play or player.can_pause
        self._window_play.set_sensitive(can_toggle)
        play_icon = (
            self._window_play.get_children()[0]
            if self._window_play.get_children()
            else None
        )
        if isinstance(play_icon, Gtk.Image):
            icon_name = (
                "media-playback-pause-symbolic"
                if player.status == "playing"
                else "media-playback-start-symbolic"
            )
            play_icon.set_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        tip = "Pausar" if player.status == "playing" else "Reanudar"
        self._window_play.set_tooltip_text(tip)

        has_progress = player.duration_usec > 0
        self._window_progress_box.set_visible(has_progress)
        if has_progress:
            ratio = min(1.0, max(0.0, player.position_usec / player.duration_usec))
            self._window_progress.set_fraction(ratio)
            self._window_elapsed.set_text(format_media_time_usec(player.position_usec))
            self._window_duration.set_text(format_media_time_usec(player.duration_usec))

    def _refresh_window_idle(self) -> None:
        self._window_thumb_key = ""
        self._window_thumb.set_from_icon_name(
            "audio-volume-muted-symbolic",
            Gtk.IconSize.DIALOG,
        )
        self._window_thumb.set_pixel_size(_WINDOW_THUMB_SIZE - 16)
        self._window_kind.set_text("Fuente")
        self._window_status.set_text("Sin audio")
        self._window_title.set_text("Ninguna fuente activa")
        self._window_detail.set_text("Elige Automático o una app cuando haya sonido")
        self._window_detail.set_visible(True)
        self._window_play.set_sensitive(False)
        self._window_progress_box.set_visible(False)
    def _transport_button(
        self,
        icon_name: str,
        callback: Callable[[], None],
        *,
        primary: bool = False,
    ) -> Gtk.Button:
        button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("media-popup-control")
        if primary:
            button.get_style_context().add_class("media-popup-control-primary")
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        button.add(image)
        button.connect("clicked", lambda _btn: callback())
        return button

    def _sync_mode_buttons(self) -> None:
        mode = self._service.display_mode
        self._mode_window.handler_block_by_func(self._on_mode_window_toggled)
        self._mode_player.handler_block_by_func(self._on_mode_player_toggled)
        if mode == MEDIA_DISPLAY_WINDOW:
            self._mode_window.set_active(True)
        else:
            self._mode_player.set_active(True)
        self._mode_window.handler_unblock_by_func(self._on_mode_window_toggled)
        self._mode_player.handler_unblock_by_func(self._on_mode_player_toggled)

    def _on_mode_window_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self._service.set_display_mode(MEDIA_DISPLAY_WINDOW)

    def _on_mode_player_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self._service.set_display_mode(MEDIA_DISPLAY_PLAYER)

    def _sync_source_picker(self, snapshot: MediaSnapshot) -> None:
        window_players = window_mode_players(snapshot.players)
        show_source = len(window_players) > 0
        self._source_row.set_visible(show_source)
        if not show_source:
            return

        active_id = self._service.manual_player or MEDIA_AUTO_PLAYER_ID
        if active_id != MEDIA_AUTO_PLAYER_ID and any(
            is_strawberry_player(player) and player.bus_name == active_id
            for player in snapshot.players
        ):
            active_id = MEDIA_AUTO_PLAYER_ID
        if active_id == MEDIA_AUTO_PLAYER_ID:
            label = "Automático"
        else:
            active = snapshot.active
            label = (
                (active.identity if active else "")
                or active_id.removeprefix("org.mpris.MediaPlayer2.")
            )
        self._source_button_label.set_text(label)

        wanted = [MEDIA_AUTO_PLAYER_ID, *[p.bus_name for p in window_players]]
        current = list(self._source_rows.keys())
        if wanted != current:
            for child in list(self._source_list.get_children()):
                self._source_list.remove(child)
            self._source_rows.clear()
            self._source_rows[MEDIA_AUTO_PLAYER_ID] = self._make_source_row(
                MEDIA_AUTO_PLAYER_ID,
                "Automático",
                "Navegador / vídeo (sin Strawberry)",
                active=(active_id == MEDIA_AUTO_PLAYER_ID),
            )
            self._source_list.add(self._source_rows[MEDIA_AUTO_PLAYER_ID])
            for player in window_players:
                detail = player.title or media_status_label(player.status)
                if player.artist:
                    detail = f"{player.artist} · {detail}" if player.title else player.artist
                row = self._make_source_row(
                    player.bus_name,
                    player.identity
                    or player.bus_name.removeprefix("org.mpris.MediaPlayer2."),
                    detail,
                    active=(active_id == player.bus_name),
                    playing=player.is_playing,
                )
                self._source_rows[player.bus_name] = row
                self._source_list.add(row)
            self._source_list.show_all()
        else:
            for bus_name, row in self._source_rows.items():
                style = row.get_style_context()
                if bus_name == active_id:
                    style.add_class("media-popup-source-active")
                else:
                    style.remove_class("media-popup-source-active")

    def _make_source_row(
        self,
        bus_name: str,
        title: str,
        detail: str,
        *,
        active: bool,
        playing: bool = False,
    ) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(True)
        row._media_bus_name = bus_name  # type: ignore[attr-defined]
        row.get_style_context().add_class("media-popup-source-row-item")
        if active:
            row.get_style_context().add_class("media-popup-source-active")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        icon_name = (
            "media-playback-start-symbolic"
            if playing
            else "audio-x-generic-symbolic"
        )
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        box.pack_start(icon, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        name = Gtk.Label(label=title, xalign=0)
        name.get_style_context().add_class("media-popup-source-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(28)
        sub = Gtk.Label(label=detail, xalign=0)
        sub.get_style_context().add_class("media-popup-source-detail")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(28)
        text.pack_start(name, False, False, 0)
        text.pack_start(sub, False, False, 0)
        box.pack_start(text, True, True, 0)
        row.add(box)
        return row

    def _on_source_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        bus_name = getattr(row, "_media_bus_name", None)
        if bus_name == MEDIA_AUTO_PLAYER_ID:
            self._service.set_auto_player_selection()
        elif bus_name:
            self._service.set_active_player(str(bus_name))
        self._source_popover.popdown()

    def _apply_artwork(
        self,
        image: Gtk.Image,
        player: MediaPlayerSnapshot,
        size: int,
        key_attr: str,
    ) -> None:
        path = player.artwork_path.strip()
        key = f"{player.bus_name}:{path or 'icon:' + player.status}:{size}"
        if key == getattr(self, key_attr):
            return
        setattr(self, key_attr, key)
        if path and Path(path).is_file():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
                image.set_from_pixbuf(scale_artwork_pixbuf(pixbuf, size))
                return
            except GLib.Error:
                setattr(self, key_attr, "")
        icon = (
            "media-playback-pause-symbolic"
            if player.status == "paused"
            else "audio-x-generic-symbolic"
        )
        image.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
        image.set_pixel_size(max(24, size - 16 if size < 120 else size))

    def _sync_progress(self, player: MediaPlayerSnapshot) -> None:
        has_progress = player.duration_usec > 0
        self._progress_box.set_visible(has_progress)
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

    def _sync_loop(self, player: MediaPlayerSnapshot) -> None:
        self._loop_button.set_sensitive(player.can_control)
        style = self._loop_button.get_style_context()
        if player.loop_status == "None":
            style.remove_class("media-popup-loop-active")
            self._loop_button.set_tooltip_text("Repetición: desactivada")
        else:
            style.add_class("media-popup-loop-active")
            tip = "pista" if player.loop_status == "Track" else "lista"
            self._loop_button.set_tooltip_text(f"Repetición: {tip}")
        icon = (
            self._loop_button.get_children()[0]
            if self._loop_button.get_children()
            else None
        )
        if isinstance(icon, Gtk.Image):
            name = (
                "media-playlist-repeat-song-symbolic"
                if player.loop_status == "Track"
                else "media-playlist-repeat-symbolic"
            )
            icon.set_from_icon_name(name, Gtk.IconSize.BUTTON)

    def _sync_volume(self, player: MediaPlayerSnapshot) -> None:
        self._volume.set_sensitive(player.can_control)
        if self._volume_dragging:
            return
        self._updating_volume = True
        self._volume.set_value(player.volume)
        self._updating_volume = False
        if player.volume <= 0.001:
            icon = "audio-volume-muted-symbolic"
        elif player.volume < 0.34:
            icon = "audio-volume-low-symbolic"
        elif player.volume < 0.67:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self._volume_icon.set_from_icon_name(icon, Gtk.IconSize.BUTTON)

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

    def _on_volume_press(self, _scale: Gtk.Scale, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._volume_dragging = True
        return False

    def _on_volume_release(self, scale: Gtk.Scale, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._volume_dragging = False
            self._service.set_volume(scale.get_value())
        return False

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        if self._updating_volume or not self._volume_dragging:
            return
        self._service.set_volume(scale.get_value())

    def _position_after_show(self) -> bool:
        if self._anchor is None:
            return False
        # Always derive top from the bar anchor so height changes grow downward.
        position_popup_below_anchor(
            self,
            self._anchor,
            title=TITLE_MEDIA_POPUP,
            offset=MEDIA_POPUP_OFFSET,
            fixed_top=None,
        )
        return False


def _source_kind_label(player: MediaPlayerSnapshot) -> str:
    blob = f"{player.bus_name} {player.identity}".casefold()
    if "strawberry" in blob or "spotify" in blob or "rhythmbox" in blob:
        return "Música"
    if any(token in blob for token in ("firefox", "chromium", "chrome", "brave", "librewolf")):
        return "Navegador"
    if any(token in blob for token in ("vlc", "mpv", "totem", "celluloid")):
        return "Vídeo"
    return "Fuente"
