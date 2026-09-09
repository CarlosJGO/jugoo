"""Settings Center widgets package."""

from .black_hole_icon import BlackHoleIcon
from .overlay import SettingsOverlay
from .pane import SEARCH_DESTINATION, build_settings_nav, show_settings_category

__all__ = [
    "BlackHoleIcon",
    "SettingsOverlay",
    "SEARCH_DESTINATION",
    "build_settings_nav",
    "show_settings_category",
]
