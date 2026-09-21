"""Structured shortcut entries for the read-only Atajos settings page.

Future editor phases can reuse this model (change keys, add/remove, conflicts)
without inventing a second catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ShortcutSource = Literal["hyprland", "jugoo", "application", "system"]

SOURCE_LABELS: dict[ShortcutSource, str] = {
    "hyprland": "Hyprland",
    "jugoo": "Jugoo",
    "application": "aplicación",
    "system": "sistema",
}

# Display order for grouped sections in the Settings UI.
CATEGORY_ORDER: tuple[str, ...] = (
    "Navegación",
    "Ventanas",
    "Aplicaciones",
    "Jugoo",
    "Multimedia",
    "Sistema",
    "Otros",
)


@dataclass(frozen=True, slots=True)
class ShortcutEntry:
    """One keyboard shortcut as shown in Configuraciones → Atajos."""

    id: str
    keys: str
    description: str
    category: str
    source: ShortcutSource
    action: str
    raw_dispatcher: str = ""
    raw_arg: str = ""
    submap: str = ""
    # Reserved for a future editor; ignored by the read-only viewer.
    editable: bool = False


@dataclass(frozen=True, slots=True)
class ShortcutSnapshot:
    """Immutable result of loading the registry once."""

    entries: tuple[ShortcutEntry, ...]
    duplicates: tuple[str, ...] = ()
    source_note: str = ""
