"""High-level API for managing user application shortcuts."""

from __future__ import annotations

from pathlib import Path

from ...models import normalize_desktop_id
from .hypr_sync import sync_user_app_shortcuts_to_hypr
from .hyprland import (
    fetch_hyprctl_binds_json,
    parse_hyprctl_binds,
    raw_bind_to_entry,
)
from .keys import keys_signature
from .user_apps import (
    PROTECTED_KEY_SIGNATURES,
    UserAppShortcut,
    load_user_app_shortcuts,
    new_shortcut_id,
    normalize_hypr_key,
    normalize_mods,
    save_user_app_shortcuts,
    validate_chord,
)


class UserAppShortcutError(ValueError):
    """Validation / conflict error for the manager UI."""


def list_user_app_shortcuts(path: Path | None = None) -> tuple[UserAppShortcut, ...]:
    return load_user_app_shortcuts(path)


def add_user_app_shortcut(
    *,
    app_id: str,
    app_name: str,
    mods: tuple[str, ...],
    key: str,
    path: Path | None = None,
    conf_path: Path | None = None,
    sync: bool = True,
    reload: bool = True,
) -> UserAppShortcut:
    shortcut = UserAppShortcut(
        id=new_shortcut_id(),
        app_id=normalize_desktop_id(app_id),
        app_name=(app_name or app_id).strip(),
        mods=normalize_mods(mods),
        key=normalize_hypr_key(key),
    )
    _assert_valid(shortcut)
    current = list(load_user_app_shortcuts(path))
    _assert_no_conflict(shortcut, current, ignore_id=None)
    current.append(shortcut)
    save_user_app_shortcuts(tuple(current), path)
    if sync:
        sync_user_app_shortcuts_to_hypr(tuple(current), conf_path=conf_path, reload=reload)
    return shortcut


def update_user_app_shortcut(
    shortcut_id: str,
    *,
    app_id: str | None = None,
    app_name: str | None = None,
    mods: tuple[str, ...] | None = None,
    key: str | None = None,
    path: Path | None = None,
    conf_path: Path | None = None,
    sync: bool = True,
    reload: bool = True,
) -> UserAppShortcut:
    current = list(load_user_app_shortcuts(path))
    index = next((i for i, item in enumerate(current) if item.id == shortcut_id), -1)
    if index < 0:
        raise UserAppShortcutError("Atajo no encontrado.")
    old = current[index]
    updated = UserAppShortcut(
        id=old.id,
        app_id=normalize_desktop_id(app_id) if app_id is not None else old.app_id,
        app_name=(app_name if app_name is not None else old.app_name).strip() or old.app_name,
        mods=normalize_mods(mods) if mods is not None else old.mods,
        key=normalize_hypr_key(key) if key is not None else old.key,
    )
    _assert_valid(updated)
    _assert_no_conflict(updated, current, ignore_id=old.id)
    current[index] = updated
    save_user_app_shortcuts(tuple(current), path)
    if sync:
        sync_user_app_shortcuts_to_hypr(tuple(current), conf_path=conf_path, reload=reload)
    return updated


def remove_user_app_shortcut(
    shortcut_id: str,
    *,
    path: Path | None = None,
    conf_path: Path | None = None,
    sync: bool = True,
    reload: bool = True,
) -> bool:
    current = list(load_user_app_shortcuts(path))
    next_items = tuple(item for item in current if item.id != shortcut_id)
    if len(next_items) == len(current):
        return False
    save_user_app_shortcuts(next_items, path)
    if sync:
        sync_user_app_shortcuts_to_hypr(next_items, conf_path=conf_path, reload=reload)
    return True


def _assert_valid(shortcut: UserAppShortcut) -> None:
    if not shortcut.app_id:
        raise UserAppShortcutError("Selecciona una aplicación.")
    error = validate_chord(shortcut.mods, shortcut.key)
    if error:
        raise UserAppShortcutError(error)


def _assert_no_conflict(
    shortcut: UserAppShortcut,
    existing: list[UserAppShortcut],
    *,
    ignore_id: str | None,
) -> None:
    signature = shortcut.signature
    if signature in PROTECTED_KEY_SIGNATURES:
        raise UserAppShortcutError(
            f"La combinación {shortcut.keys_display} está reservada por el sistema."
        )
    for item in existing:
        if ignore_id and item.id == ignore_id:
            continue
        if item.signature == signature:
            raise UserAppShortcutError(
                f"Ya existe un atajo de aplicación en {shortcut.keys_display}."
            )

    managed_signatures = {
        item.signature
        for item in existing
        if not (ignore_id and item.id == ignore_id)
    }
    for keys, description in _live_hypr_chords():
        other = keys_signature(keys)
        if other != signature:
            continue
        if other in managed_signatures:
            # Already owned by the JSON catalog (will be rewritten on sync).
            continue
        raise UserAppShortcutError(
            f"Conflicto con un atajo existente: {keys} ({description})."
        )


def _live_hypr_chords() -> tuple[tuple[str, str], ...]:
    payload = fetch_hyprctl_binds_json()
    if payload is None:
        return ()
    result: list[tuple[str, str]] = []
    for index, bind in enumerate(parse_hyprctl_binds(payload)):
        entry = raw_bind_to_entry(bind, index=index)
        if entry is None:
            continue
        # Ignore gtk-launch binds that already mirror managed user shortcuts.
        if entry.raw_dispatcher == "exec" and "gtk-launch" in (entry.raw_arg or ""):
            continue
        result.append((entry.keys, entry.description))
    return tuple(result)
