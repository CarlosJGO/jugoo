"""Keyboard shortcut catalog for Configuraciones → Atajos (read-only + app manager)."""

from .labels import MONOCLE_STACK_NOTE
from .manager import (
    UserAppShortcutError,
    add_user_app_shortcut,
    list_user_app_shortcuts,
    remove_user_app_shortcut,
    update_user_app_shortcut,
)
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
    merge_monocle_stack_pairs,
    merge_user_app_shortcuts,
    sort_entries,
)
from .user_apps import (
    PROTECTED_KEY_SIGNATURES,
    UserAppShortcut,
    load_user_app_shortcuts,
    validate_chord,
)

__all__ = (
    "CATEGORY_ORDER",
    "MONOCLE_STACK_NOTE",
    "PROTECTED_KEY_SIGNATURES",
    "SOURCE_LABELS",
    "ShortcutEntry",
    "ShortcutRegistry",
    "ShortcutSnapshot",
    "ShortcutSource",
    "UserAppShortcut",
    "UserAppShortcutError",
    "add_user_app_shortcut",
    "filter_entries",
    "find_duplicate_keys",
    "group_by_category",
    "list_user_app_shortcuts",
    "load_shortcuts",
    "load_user_app_shortcuts",
    "merge_monocle_stack_pairs",
    "merge_user_app_shortcuts",
    "remove_user_app_shortcut",
    "sort_entries",
    "update_user_app_shortcut",
    "validate_chord",
)
