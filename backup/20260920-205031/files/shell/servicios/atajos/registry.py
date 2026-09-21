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
from .model import CATEGORY_ORDER, ShortcutEntry, ShortcutSnapshot

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

        ordered = sort_entries(tuple(entries))
        return ShortcutSnapshot(
            entries=ordered,
            duplicates=find_duplicate_keys(ordered),
            source_note=note,
        )


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
