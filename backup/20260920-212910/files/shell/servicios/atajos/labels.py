"""Friendly labels for binds — informational only, never executes commands."""

from __future__ import annotations

import re
from pathlib import Path

from ...actions import ACTIONS
from .model import ShortcutSource

_ACTIONS_BY_NAME = {action.name: action for action in ACTIONS}

# Spanish labels for Jugoo shell actions (ACTIONS descriptions stay English for CLI).
_JUGOO_LABELS: dict[str, str] = {
    "ask": "Abrir prompt de IA",
    "launcher": "Lanzador de aplicaciones",
    "clipboard": "Historial del portapapeles",
    "emoji": "Selector de emoji",
    "playStopMusic": "Reproducir / pausar música",
    "musicVolumeUp": "Subir volumen del reproductor",
    "musicVolumeDown": "Bajar volumen del reproductor",
    "media": "Panel multimedia",
    "settings": "Configuraciones",
    "control-center": "Centro de control",
    "notifications": "Historial de notificaciones",
    "session": "Menú de sesión / energía",
    "tasks": "Panel de tareas",
    "reload-theme": "Recargar tema",
    "sddm-apply": "Aplicar tema SDDM",
    "sddm-restore": "Restaurar tema SDDM",
}

_JUGOO_CATEGORIES: dict[str, str] = {
    "ask": "Jugoo",
    "launcher": "Aplicaciones",
    "clipboard": "Jugoo",
    "emoji": "Jugoo",
    "playStopMusic": "Multimedia",
    "musicVolumeUp": "Multimedia",
    "musicVolumeDown": "Multimedia",
    "media": "Multimedia",
    "settings": "Jugoo",
    "control-center": "Jugoo",
    "notifications": "Jugoo",
    "session": "Sistema",
    "tasks": "Jugoo",
    "reload-theme": "Jugoo",
    "sddm-apply": "Sistema",
    "sddm-restore": "Sistema",
}

_DISPATCHER_LABELS: dict[tuple[str, str], tuple[str, str, ShortcutSource]] = {
    # (dispatcher, arg_prefix_or_exact) → description, category, source
    ("killactive", ""): ("Cerrar ventana", "Ventanas", "hyprland"),
    ("fullscreen", ""): ("Pantalla completa", "Ventanas", "hyprland"),
    ("togglefloating", ""): ("Alternar flotante", "Ventanas", "hyprland"),
    ("exit", ""): ("Salir de Hyprland", "Sistema", "system"),
    ("layoutmsg", "cyclenext"): ("Siguiente ventana", "Navegación", "hyprland"),
    ("layoutmsg", "cycleprev"): ("Ventana anterior", "Navegación", "hyprland"),
    ("bringactivetotop", ""): ("Traer al frente", "Ventanas", "hyprland"),
}

# Display-only note when SUPER+J/K compose layoutmsg + bringactivetotop (FASE C.1.1).
MONOCLE_STACK_NOTE = "Monocle · eleva la ventana activa"

# layoutmsg action → companion dispatcher that may share the same keys.
MONOCLE_STACK_COMPOSE: dict[str, str] = {
    "cyclenext": "bringactivetotop",
    "cycleprev": "bringactivetotop",
}

_APP_BASENAME_LABELS: dict[str, str] = {
    "firefox": "Firefox",
    "firefox-esr": "Firefox",
    "xfce4-terminal": "Terminal",
    "kitty": "Terminal",
    "alacritty": "Terminal",
    "foot": "Terminal",
    "wezterm": "Terminal",
    "gnome-terminal": "Terminal",
    "thunar": "Administrador de archivos",
    "nautilus": "Administrador de archivos",
    "dolphin": "Administrador de archivos",
    "code": "VS Code",
    "cursor": "Cursor",
}

_JUGOO_ACTION_RE = re.compile(
    r"(?:^|[/\s\"'])jugoo(?:\.py)?\b.*?\baction\s+([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_JUGOO_ACTION_SIMPLE_RE = re.compile(
    r"\baction\s+([A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)


def classify_bind(
    dispatcher: str,
    arg: str,
) -> tuple[str, str, ShortcutSource, str]:
    """Return ``(description, category, source, action_id)`` for a raw bind.

    ``arg`` is treated as opaque text. Nothing is executed.
    """
    disp = (dispatcher or "").strip()
    raw_arg = (arg or "").strip()
    arg_key = raw_arg.split(",", 1)[0].strip()

    if disp == "workspace":
        return (
            f"Workspace {arg_key or '?'}",
            "Navegación",
            "hyprland",
            f"workspace:{arg_key}",
        )
    if disp == "movetoworkspace":
        return (
            f"Mover a workspace {arg_key or '?'}",
            "Navegación",
            "hyprland",
            f"movetoworkspace:{arg_key}",
        )
    if disp == "movetoworkspacesilent":
        return (
            f"Mover (silencioso) a workspace {arg_key or '?'}",
            "Navegación",
            "hyprland",
            f"movetoworkspacesilent:{arg_key}",
        )

    for (known_disp, known_arg), (label, category, source) in _DISPATCHER_LABELS.items():
        if disp != known_disp:
            continue
        if known_arg == "" or arg_key == known_arg or raw_arg.startswith(known_arg):
            return label, category, source, known_arg or known_disp

    if disp == "exec":
        return _classify_exec(raw_arg)

    # Unknown dispatcher: safe truncated representation.
    safe = _safe_summary(disp, raw_arg)
    return safe, "Otros", "hyprland", disp or "unknown"


def jugoo_action_label(name: str) -> str | None:
    if name in _JUGOO_LABELS:
        return _JUGOO_LABELS[name]
    action = _ACTIONS_BY_NAME.get(name)
    if action is not None:
        return action.description
    return None


def known_jugoo_action_names() -> tuple[str, ...]:
    return tuple(action.name for action in ACTIONS)


def _classify_exec(raw_arg: str) -> tuple[str, str, ShortcutSource, str]:
    jugoo_name = _extract_jugoo_action(raw_arg)
    if jugoo_name is not None:
        label = jugoo_action_label(jugoo_name) or f"Acción Jugoo: {jugoo_name}"
        category = _JUGOO_CATEGORIES.get(jugoo_name, "Jugoo")
        return label, category, "jugoo", jugoo_name

    basename = _exec_basename(raw_arg)
    if basename in _APP_BASENAME_LABELS:
        return _APP_BASENAME_LABELS[basename], "Aplicaciones", "application", basename
    if basename and _looks_like_binary_name(basename, raw_arg):
        return f"Abrir {basename}", "Aplicaciones", "application", basename

    safe = _truncate(raw_arg, 48) or "comando"
    return f"Ejecutar: {safe}", "Aplicaciones", "application", "exec"


def _extract_jugoo_action(raw_arg: str) -> str | None:
    match = _JUGOO_ACTION_RE.search(raw_arg)
    if match:
        return match.group(1)
    # Packaging lua wraps: ... jugoo " action ask " via sh -c
    if "jugoo" in raw_arg.casefold():
        simple = _JUGOO_ACTION_SIMPLE_RE.search(raw_arg)
        if simple:
            return simple.group(1)
    return None


def _exec_basename(raw_arg: str) -> str:
    """Best-effort binary name from opaque exec text (never executed)."""
    text = raw_arg or ""
    lowered = text.casefold()

    # Prefer known application basenames mentioned as paths or bare commands.
    for name in _APP_BASENAME_LABELS:
        if re.search(rf"(?:^|[/\s]){re.escape(name)}(?:\s|$)", lowered):
            return name

    # Last absolute-path basename (skip wrappers like /usr/bin/env and URL paths).
    skip = {"env", "sh", "bash", "dbus-run-session"}
    last = ""
    for match in re.finditer(r"(/[\w./+-]+)", text):
        start = match.start()
        # Skip the path part of ``scheme://host…``.
        if start >= 2 and text[start - 2 : start] == ":/":
            continue
        name = Path(match.group(1)).name.casefold()
        if name and name not in skip and not name.startswith("-"):
            last = name
    if last:
        return last

    # Bare command before options: `thunar --new-window`
    for token in text.split():
        if token.startswith("-") or "=" in token or "://" in token:
            continue
        name = Path(token).name.casefold()
        if name and name not in skip:
            return name
    return ""


def _looks_like_binary_name(name: str, raw_arg: str) -> bool:
    """True when ``name`` came from a filesystem path or bare argv0."""
    text = raw_arg or ""
    for match in re.finditer(rf"/{re.escape(name)}(?:\s|$)", text):
        start = match.start()
        if start >= 2 and text[start - 2 : start] == ":/":
            continue
        return True
    first = text.strip().split(None, 1)[0] if text.strip() else ""
    if not first or "://" in first:
        return False
    return Path(first).name.casefold() == name


def _safe_summary(dispatcher: str, arg: str) -> str:
    if not arg:
        return dispatcher or "Atajo"
    return f"{dispatcher}: {_truncate(arg, 40)}"


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"
