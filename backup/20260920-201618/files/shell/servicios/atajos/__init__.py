"""Keyboard shortcut catalog for Configuraciones → Atajos (read-only)."""

from .model import (
    CATEGORY_ORDER,
    SOURCE_LABELS,
    ShortcutEntry,
    ShortcutSnapshot,
    ShortcutSource,
)
from .registry import (
    ShortcutRegistry,
    filter_entries,
    find_duplicate_keys,
    group_by_category,
    load_shortcuts,
    sort_entries,
)

__all__ = (
    "CATEGORY_ORDER",
    "SOURCE_LABELS",
    "ShortcutEntry",
    "ShortcutRegistry",
    "ShortcutSnapshot",
    "ShortcutSource",
    "filter_entries",
    "find_duplicate_keys",
    "group_by_category",
    "load_shortcuts",
    "sort_entries",
)
