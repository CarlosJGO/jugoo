"""Formatting helpers for media UI labels."""

from __future__ import annotations

from ...models import ActiveWindow, MediaPlayerSnapshot


def format_media_time_usec(usec: int) -> str:
    total_sec = max(0, int(usec) // 1_000_000)
    minutes, seconds = divmod(total_sec, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def media_status_glyph(status: str) -> str:
    return {
        "playing": "▶",
        "paused": "⏸",
        "stopped": "■",
    }.get(status, "")


def media_status_label(status: str) -> str:
    return {
        "playing": "Reproduciendo",
        "paused": "Pausado",
        "stopped": "Detenido",
    }.get(status, status.capitalize())


def compact_bar_primary(player: MediaPlayerSnapshot) -> str:
    return player.title or player.identity or "Reproducción multimedia"


def compact_bar_secondary(player: MediaPlayerSnapshot) -> str:
    if player.artist and player.album:
        return f"{player.artist} · {player.album}"
    return player.artist or player.album or player.identity


def window_bar_primary(active_window: ActiveWindow) -> str:
    return active_window.application_name or active_window.app_class


def window_bar_secondary(active_window: ActiveWindow) -> str:
    return active_window.title
