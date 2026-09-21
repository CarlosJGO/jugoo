"""Key combination formatting for Hyprland modmasks and conf tokens."""

from __future__ import annotations

# X11 / Hyprland modmask bits.
_MOD_SHIFT = 1
_MOD_CTRL = 4
_MOD_ALT = 8
_MOD_SUPER = 64

_MOD_ORDER: tuple[tuple[int, str], ...] = (
    (_MOD_SUPER, "SUPER"),
    (_MOD_CTRL, "CTRL"),
    (_MOD_ALT, "ALT"),
    (_MOD_SHIFT, "SHIFT"),
)

_KEY_ALIASES: dict[str, str] = {
    "return": "ENTER",
    "space": "SPACE",
    "escape": "ESC",
    "period": ".",
    "comma": ",",
    "minus": "-",
    "equal": "=",
    "slash": "/",
    "backslash": "\\",
    "apostrophe": "'",
    "grave": "`",
    "bracketleft": "[",
    "bracketright": "]",
    "semicolon": ";",
    "tab": "TAB",
    "backspace": "BACKSPACE",
    "delete": "DEL",
    "insert": "INS",
    "home": "HOME",
    "end": "END",
    "pageup": "PGUP",
    "pagedown": "PGDN",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
}


def format_modmask_keys(modmask: int, key: str) -> str:
    """Turn a Hyprland ``modmask`` + ``key`` into ``SUPER + SHIFT + F``."""
    parts = [label for bit, label in _MOD_ORDER if int(modmask) & bit]
    parts.append(_normalize_key(key))
    return " + ".join(parts)


def format_conf_keys(mods: str, key: str) -> str:
    """Turn conf tokens ``SUPER SHIFT`` + ``F`` into ``SUPER + SHIFT + F``."""
    parts = [token.strip().upper() for token in mods.replace("+", " ").split() if token.strip()]
    # Normalize common aliases used in conf files.
    normalized: list[str] = []
    for part in parts:
        if part in {"MOD", "MOD4", "WIN", "META"}:
            normalized.append("SUPER")
        elif part in {"CONTROL", "CTL"}:
            normalized.append("CTRL")
        elif part in {"MOD1"}:
            normalized.append("ALT")
        else:
            normalized.append(part)
    # Stable order matching format_modmask_keys.
    order = {"SUPER": 0, "CTRL": 1, "ALT": 2, "SHIFT": 3}
    normalized = sorted(set(normalized), key=lambda item: order.get(item, 50))
    normalized.append(_normalize_key(key))
    return " + ".join(normalized)


def keys_signature(keys: str) -> str:
    """Comparable signature for duplicate detection (order-independent mods)."""
    parts = [part.strip().upper() for part in keys.split("+") if part.strip()]
    if not parts:
        return ""
    *mods, key = parts
    order = {"SUPER": 0, "CTRL": 1, "ALT": 2, "SHIFT": 3}
    mods_sorted = sorted(mods, key=lambda item: order.get(item, 50))
    return "+".join(mods_sorted + [key])


def _normalize_key(key: str) -> str:
    raw = (key or "").strip()
    if not raw:
        return "?"
    lower = raw.casefold()
    if lower in _KEY_ALIASES:
        return _KEY_ALIASES[lower]
    if lower.startswith("mouse:"):
        return raw.upper()
    if len(raw) == 1:
        return raw.upper()
    # Function keys and named keys: F10, XF86AudioRaiseVolume, …
    if lower.startswith("f") and lower[1:].isdigit():
        return raw.upper()
    if lower.startswith("xf86"):
        return raw
    return raw.upper() if raw.isalpha() and len(raw) <= 3 else raw
