"""Pure geometry for retracting the top bar away from floating windows."""

from __future__ import annotations

from typing import Sequence

from ...models import FloatingClient


def compute_bar_retract_px(
    *,
    bar_top: int,
    bar_height: int,
    bar_left: int,
    bar_width: int,
    gap_px: int,
    clients: Sequence[FloatingClient],
    ignore_classes: Sequence[str] = (),
) -> int:
    """Return how many pixels to pull the bar upward (0 … bar_height).

    A floating window whose top edge enters the bar strip causes the bar to
    retract just enough that the visible bar bottom clears that top edge
    (minus ``gap_px``). Pushing the window fully into the strip retracts the
    whole bar.
    """
    if bar_height <= 0 or bar_width <= 0:
        return 0

    bar_bottom = bar_top + bar_height
    bar_right = bar_left + bar_width
    ignored = {str(name).casefold() for name in ignore_classes if str(name).strip()}
    gap = max(0, int(gap_px))
    needed = 0

    for client in clients:
        if client.width <= 0 or client.height <= 0:
            continue
        if int(client.fullscreen or 0) != 0:
            continue
        if client.app_class.casefold() in ignored:
            continue

        client_right = client.x + client.width
        client_bottom = client.y + client.height
        if client_right <= bar_left or client.x >= bar_right:
            continue
        if client_bottom <= bar_top or client.y >= bar_bottom:
            continue

        # Clear the window's top edge with a small breathing gap.
        clear_y = client.y - gap
        retract = bar_bottom - clear_y
        if retract > needed:
            needed = retract

    if needed <= 0:
        return 0
    return min(int(needed), bar_height)


def step_retract_px(*, current: float, target: float) -> float:
    """Ease ``current`` toward ``target`` for a soft retract animation."""
    delta = float(target) - float(current)
    if abs(delta) < 0.5:
        return float(target)
    # Fast enough to feel responsive while dragging; never stalls near the end.
    step = max(2.0, abs(delta) * 0.42)
    if delta > 0:
        return min(float(target), float(current) + step)
    return max(float(target), float(current) - step)
