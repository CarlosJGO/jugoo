"""User-managed application shortcuts (JSON source of truth).

Hyprland receives a synced managed bind block; the compositor is never asked to
execute arbitrary text from the settings UI beyond ``gtk-launch <desktop-id>``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from ...models import normalize_desktop_id
from ...runtime_paths import user_app_shortcuts_path
from .keys import format_conf_keys, keys_signature

_STORE_VERSION = 1
_MAX_SHORTCUTS = 64
_VALID_MODS = frozenset({"SUPER", "CTRL", "ALT", "SHIFT"})
# Hyprland conf key token (letters, digits, named keys).
_KEY_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$|^[0-9]$|^Return$|^Space$|^Escape$|^Tab$|^period$|^comma$")

# System / Jugoo-critical chords that the manager must never overwrite.
PROTECTED_KEY_SIGNATURES: frozenset[str] = frozenset(
    {
        *(f"SUPER+{n}" for n in range(1, 10)),
        *(f"SUPER+SHIFT+{n}" for n in range(1, 10)),
        "SUPER+F",
        "SUPER+SHIFT+F",
        "SUPER+J",
        "SUPER+K",
        "SUPER+Q",
        "SUPER+SHIFT+Q",
        "SUPER+W",
        "SUPER+SPACE",
        "SUPER+ENTER",
        "SUPER+E",  # existing file manager bind in the stock conf
    }
)


@dataclass(frozen=True, slots=True)
class UserAppShortcut:
    """One user-defined application keybind."""

    id: str
    app_id: str
    app_name: str
    mods: tuple[str, ...]
    key: str  # Hyprland conf key token (e.g. B, Return, Space)

    @property
    def keys_display(self) -> str:
        return format_conf_keys(" ".join(self.mods), self.key)

    @property
    def signature(self) -> str:
        return keys_signature(self.keys_display)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "app_id": self.app_id,
            "app_name": self.app_name,
            "mods": list(self.mods),
            "key": self.key,
        }

    def hypr_bind_line(self) -> str:
        """Conf line that launches the desktop app via gtk-launch (never raw shell)."""
        mods = " ".join(self.mods) if self.mods else "SUPER"
        app = normalize_desktop_id(self.app_id)
        return f"bind = {mods}, {self.key}, exec, gtk-launch {app}"


def new_shortcut_id() -> str:
    return uuid.uuid4().hex[:12]


def normalize_mods(mods: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    order = {"SUPER": 0, "CTRL": 1, "ALT": 2, "SHIFT": 3}
    cleaned: list[str] = []
    for item in mods:
        token = str(item or "").strip().upper()
        if token in {"MOD", "MOD4", "WIN", "META"}:
            token = "SUPER"
        elif token in {"CONTROL", "CTL"}:
            token = "CTRL"
        elif token in {"MOD1"}:
            token = "ALT"
        if token in _VALID_MODS and token not in cleaned:
            cleaned.append(token)
    return tuple(sorted(cleaned, key=lambda item: order.get(item, 50)))


def normalize_hypr_key(key: str) -> str:
    raw = (key or "").strip()
    if not raw:
        return ""
    lower = raw.casefold()
    aliases = {
        "enter": "Return",
        "return": "Return",
        "space": "Space",
        "esc": "Escape",
        "escape": "Escape",
        "tab": "Tab",
        ".": "period",
        ",": "comma",
    }
    if lower in aliases:
        return aliases[lower]
    if len(raw) == 1 and raw.isalnum():
        return raw.upper()
    if lower.startswith("f") and lower[1:].isdigit():
        return raw.upper()
    return raw


def shortcut_from_dict(raw: object) -> UserAppShortcut | None:
    if not isinstance(raw, dict):
        return None
    ident = str(raw.get("id") or "").strip()
    app_id = normalize_desktop_id(str(raw.get("app_id") or ""))
    app_name = str(raw.get("app_name") or "").strip() or app_id
    mods = normalize_mods(tuple(raw.get("mods") or ()))
    key = normalize_hypr_key(str(raw.get("key") or ""))
    if not ident or not app_id or not mods or not key:
        return None
    if not _KEY_TOKEN_RE.match(key) and len(key) != 1:
        # Allow single printable keys already normalized.
        if not (len(key) == 1 and key.isprintable()):
            return None
    return UserAppShortcut(
        id=ident,
        app_id=app_id,
        app_name=app_name,
        mods=mods,
        key=key,
    )


def load_user_app_shortcuts(path: Path | None = None) -> tuple[UserAppShortcut, ...]:
    store = path if path is not None else user_app_shortcuts_path()
    try:
        text = store.read_text(encoding="utf-8")
    except OSError:
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict):
        return ()
    items = payload.get("shortcuts")
    if not isinstance(items, list):
        return ()
    result: list[UserAppShortcut] = []
    seen: set[str] = set()
    for raw in items:
        shortcut = shortcut_from_dict(raw)
        if shortcut is None or shortcut.id in seen:
            continue
        seen.add(shortcut.id)
        result.append(shortcut)
        if len(result) >= _MAX_SHORTCUTS:
            break
    return tuple(result)


def save_user_app_shortcuts(
    shortcuts: tuple[UserAppShortcut, ...],
    path: Path | None = None,
) -> None:
    store = path if path is not None else user_app_shortcuts_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _STORE_VERSION,
        "shortcuts": [item.to_dict() for item in shortcuts[:_MAX_SHORTCUTS]],
    }
    tmp = store.with_suffix(store.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(store)


def is_protected_signature(signature: str) -> bool:
    return keys_signature(signature) in PROTECTED_KEY_SIGNATURES or (
        signature.replace(" ", "") in PROTECTED_KEY_SIGNATURES
    )


def validate_chord(mods: tuple[str, ...], key: str) -> str | None:
    """Return an error message or ``None`` if the chord is usable."""
    normalized_mods = normalize_mods(mods)
    normalized_key = normalize_hypr_key(key)
    if not normalized_mods:
        return "Se requiere al menos un modificador (p. ej. SUPER)."
    if not normalized_key:
        return "Se requiere una tecla."
    if SUPER_REQUIRED and "SUPER" not in normalized_mods:
        return "Los atajos de aplicación deben incluir SUPER."
    display = format_conf_keys(" ".join(normalized_mods), normalized_key)
    signature = keys_signature(display)
    if signature in PROTECTED_KEY_SIGNATURES:
        return f"La combinación {display} está reservada por el sistema."
    return None


# App shortcuts always require SUPER to avoid clobbering plain typing.
SUPER_REQUIRED = True


def occupied_signatures_from_hypr(
    entries_keys: tuple[str, ...],
    *,
    ignore_signatures: frozenset[str] = frozenset(),
) -> set[str]:
    occupied: set[str] = set()
    for keys in entries_keys:
        signature = keys_signature(keys)
        if signature and signature not in ignore_signatures:
            occupied.add(signature)
    return occupied
