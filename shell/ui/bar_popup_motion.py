"""Geometry helpers for a popup that opens from a bar block.

Hyprland places the window and plays its open and close animation. This module
does not resize, restyle, or repaint the window.
"""

from __future__ import annotations

Rect = tuple[int, int, int, int]


def ease_out_cubic(progress: float) -> float:
    progress = 0.0 if progress < 0.0 else 1.0 if progress > 1.0 else progress
    return 1.0 - (1.0 - progress) ** 3


def union_of(anchor: Rect, popup: Rect) -> Rect:
    """Smallest rectangle that contains the bar block and the popup."""
    ax, ay, aw, ah = anchor
    px, py, pw, ph = popup
    left = min(ax, px)
    top = min(ay, py)
    right = max(ax + aw, px + pw)
    bottom = max(ay + ah, py + ph)
    return left, top, right - left, bottom - top


def motion_rect(anchor: Rect, popup: Rect, union: Rect, progress: float) -> tuple[float, float, float, float]:
    """Card rectangle inside ``union`` at ``progress`` (0 = block, 1 = popup)."""
    t = ease_out_cubic(progress)
    ax, ay, aw, ah = anchor
    px, py, pw, ph = popup
    ux, uy, _uw, _uh = union
    x0 = float(ax - ux)
    y0 = float(ay - uy)
    x1 = float(px - ux)
    y1 = float(py - uy)
    return (
        x0 + (x1 - x0) * t,
        y0 + (y1 - y0) * t,
        float(aw) + (float(pw) - float(aw)) * t,
        float(ah) + (float(ph) - float(ah)) * t,
    )


def bar_motion_enabled() -> bool:
    from .theme import active_theme

    theme = active_theme()
    if theme is None:
        return True
    return bool(theme.animation.enabled)


def bar_popup_position_held(title: str) -> bool:
    """Jugoo does not freeze popup moves. Hyprland owns the animation."""
    del title
    return False
