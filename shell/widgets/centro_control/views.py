"""Control center view modes for single-section vs full layouts."""

from __future__ import annotations

from enum import Enum


class ControlCenterView(str, Enum):
    """Which sections to show in a control center popup."""

    FULL = "full"
    NETWORK = "network"
    AUDIO = "audio"
    MEDIA = "media"
