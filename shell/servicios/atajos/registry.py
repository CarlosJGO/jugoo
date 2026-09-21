"""Shortcut registry: load active binds and present them for Settings → Atajos."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .hyprland import (
    fetch_hyprctl_binds_json,
    load_conf_binds,
    parse_hyprctl_binds,
    raw_bind_to_entry,
)
from .keys import keys_signature
from .labels import MONOCLE_STACK_COMPOSE, MONOCLE_STACK_NOTE
from .model import CATEGORY_ORDER, ShortcutEntry, ShortcutSnapshot
from .user_apps import load_user_app_shortcuts

JsonRunner = Callable[[str], Any]


class ShortcutRegistry:
    """Single entry point for the Atajos page.

    Preferred source is the live Hyprland bind list. When Hyprland is absent,
    falls back to parsing ``hyprland.conf`` as plain text (never executed).
    """

    def __init__(
        self,
        *,
        conf_path: Path | None = None,
        hyprctl_runner: JsonRunner | None = None,
        prefer_hyprctl: bool = True,
    ) -> None:
        self._conf_path = conf_path
        self._hyprctl_runner = hyprctl_runner
        self._prefer_hyprctl = prefer_hyprctl

    def load(self) -> ShortcutSnapshot:
        entries: list[ShortcutEntry] = []
        note = ""

        if self._prefer_hyprctl:
            payload = fetch_hyprctl_binds_json(runner=self._hyprctl_runner)
            if payload is not None:
                raw = parse_hyprctl_binds(payload)
                for index, bind in enumerate(raw):
                    entry = raw_bind_to_entry(bind, index=index)
                    if entry is not None:
                        entries.append(entry)
                note = "hyprctl"
            else:
                raw = load_conf_binds(self._conf_path)
                for index, bind in enumerate(raw):
                    entry = raw_bind_to_entry(bind, index=index)
                    if entry is not None:
                        entries.append(entry)
                note = "conf-fallback" if raw else "unavailable"
        else:
            raw = load_conf_binds(self._conf_path)
            for index, bind in enumerate(raw):
                entry = raw_bind_to_entry(bind, index=index)
                if entry is not None:
                    entries.append(entry)
            note = "conf" if raw else "unavailable"

        # Physical binds stay intact in Hyprland; display may compose monocle pairs.
        display = merge_monocle_stack_pairs(tuple(entries))
        display = merge_user_app_shortcuts(display)
        ordered = sort_entries(display)
        return ShortcutSnapshot(
            entries=ordered,
            duplicates=find_duplicate_keys(ordered),
            source_note=note,
        )


def merge_user_app_shortcuts(
    entries: tuple[ShortcutEntry, ...],
    *,
    user_path: Path | None = None,
) -> tuple[ShortcutEntry, ...]:
    """Overlay JSON-managed app shortcuts onto the display catalog.

    Hyprland ``gtk-launch`` binds that match a managed chord are replaced by the
    richer user entry (editable metadata). Other binds are unchanged.
    """
    user_items = load_user_app_shortcuts(user_path)
    if not user_items:
        return entries

    user_by_sig = {item.signature: item for item in user_items}
    kept: list[ShortcutEntry] = []
    for entry in entries:
        signature = keys_signature(entry.keys)
        if signature in user_by_sig:
            continue
        # Drop raw gtk-launch rows that belong to managed apps even if keys differ.
        if entry.raw_dispatcher == "exec" and "gtk-launch" in (entry.raw_arg or ""):
            arg = (entry.raw_arg or "").casefold()
            if any(item.app_id.casefold() in arg for item in user_items):
                # Only skip when the chord is also managed; otherwise keep.
                if signature in user_by_sig:
                    continue
        kept.append(entry)

    for item in user_items:
        kept.append(
            ShortcutEntry(
                id=f"user-app:{item.id}",
                keys=item.keys_display,
                description=item.app_name or item.app_id,
                category="Aplicaciones",
                source="application",
                action=item.app_id,
                raw_dispatcher="exec",
                raw_arg=f"gtk-launch {item.app_id}",
                note="Gestionable · atajo de aplicación",
                editable=True,
            )
        )
    return tuple(kept)

def merge_monocle_stack_pairs(
    entries: tuple[ShortcutEntry, ...],
) -> tuple[ShortcutEntry, ...]:
    """Compose SUPER+J/K layoutmsg + bringactivetotop into one display row.

    Only merges when the same key chord has *both* a monocle cycle layoutmsg and
    ``bringactivetotop``. Other same-key binds are left untouched.
    """
    by_keys: dict[str, list[ShortcutEntry]] = {}
    order: list[str] = []
    for entry in entries:
        signature = keys_signature(entry.keys)
        if signature not in by_keys:
            order.append(signature)
            by_keys[signature] = []
        by_keys[signature].append(entry)

    result: list[ShortcutEntry] = []
    for signature in order:
        group = by_keys[signature]
        primary = next(
            (
                item
                for item in group
                if item.raw_dispatcher == "layoutmsg"
                and item.action in MONOCLE_STACK_COMPOSE
            ),
            None,
        )
        companion_action = (
            MONOCLE_STACK_COMPOSE.get(primary.action) if primary is not None else None
        )
        companion = None
        if primary is not None and companion_action is not None:
            companion = next(
                (
                    item
                    for item in group
                    if item is not primary
                    and (
                        item.raw_dispatcher == companion_action
                        or item.action == companion_action
                    )
                ),
                None,
            )

        if primary is not None and companion is not None:
            result.append(
                ShortcutEntry(
                    id=f"composed:monocle:{signature}:{primary.action}",
                    keys=primary.keys,
                    description=primary.description,
                    category=primary.category,
                    source=primary.source,
                    action=primary.action,
                    raw_dispatcher=primary.raw_dispatcher,
                    raw_arg=primary.raw_arg or primary.action,
                    submap=primary.submap,
                    note=MONOCLE_STACK_NOTE,
                    editable=False,
                )
            )
            for item in group:
                if item is primary or item is companion:
                    continue
                result.append(item)
            continue

        result.extend(group)

    return tuple(result)


def sort_entries(entries: tuple[ShortcutEntry, ...]) -> tuple[ShortcutEntry, ...]:
    category_rank = {name: index for index, name in enumerate(CATEGORY_ORDER)}

    def sort_key(entry: ShortcutEntry) -> tuple[int, str, str]:
        return (
            category_rank.get(entry.category, len(CATEGORY_ORDER)),
            entry.keys,
            entry.description,
        )

    return tuple(sorted(entries, key=sort_key))


def find_duplicate_keys(entries: tuple[ShortcutEntry, ...]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for entry in entries:
        signature = keys_signature(entry.keys)
        if not signature:
            continue
        count = seen.get(signature, 0) + 1
        seen[signature] = count
        if count == 2:
            duplicates.append(signature)
    return tuple(duplicates)


def group_by_category(
    entries: tuple[ShortcutEntry, ...],
) -> tuple[tuple[str, tuple[ShortcutEntry, ...]], ...]:
    buckets: dict[str, list[ShortcutEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.category, []).append(entry)

    ordered: list[tuple[str, tuple[ShortcutEntry, ...]]] = []
    for name in CATEGORY_ORDER:
        items = buckets.pop(name, None)
        if items:
            ordered.append((name, tuple(items)))
    for name in sorted(buckets):
        ordered.append((name, tuple(buckets[name])))
    return tuple(ordered)


def filter_entries(
    entries: tuple[ShortcutEntry, ...],
    *,
    query: str = "",
    category: str | None = None,
) -> tuple[ShortcutEntry, ...]:
    needle = query.strip().casefold()
    result: list[ShortcutEntry] = []
    for entry in entries:
        if category and entry.category != category:
            continue
        if needle:
            haystack = " ".join(
                (
                    entry.keys,
                    entry.description,
                    entry.category,
                    entry.source,
                    entry.action,
                    entry.note,
                )
            ).casefold()
            if needle not in haystack:
                continue
        result.append(entry)
    return tuple(result)


def load_shortcuts(
    *,
    conf_path: Path | None = None,
    hyprctl_runner: JsonRunner | None = None,
) -> ShortcutSnapshot:
    """Convenience wrapper used by the Settings UI."""
    return ShortcutRegistry(
        conf_path=conf_path,
        hyprctl_runner=hyprctl_runner,
    ).load()
