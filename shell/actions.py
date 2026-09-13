"""External action surface for a running Jugoo shell.

Hyprland (or any client) invokes the unique Gio.Application ``com.jugoo.Shell``.
The primary instance dispatches into existing ``ShellApplication`` methods — no
second GTK shell, no parallel EventBus.

Preferred CLI::

    jugoo action launcher
    jugoo action list

Legacy flags (``--toggle-launcher``, …) remain and resolve to the same names.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShellAction:
    """One externally invokable capability."""

    name: str
    description: str
    # Legacy argv tokens that map to this action (may be empty).
    legacy_flags: tuple[str, ...] = ()
    # If False, keep for CLI/compat but discourage new global binds.
    recommend_global_bind: bool = True


# Canonical action ids — keep this list small on purpose.
ACTIONS: tuple[ShellAction, ...] = (
    ShellAction(
        "launcher",
        "Toggle application launcher / Search",
        legacy_flags=("--toggle-launcher",),
    ),
    ShellAction(
        "clipboard",
        "Toggle clipboard history picker",
        legacy_flags=("--toggle-clipboard",),
    ),
    ShellAction(
        "emoji",
        "Toggle emoji picker",
        legacy_flags=("--toggle-emoji",),
    ),
    ShellAction(
        "playStopMusic",
        "Play or pause music in Strawberry",
    ),
    ShellAction(
        "media",
        "Toggle media popup (Ventana / Reproductor)",
        legacy_flags=("--toggle-media",),
    ),
    ShellAction(
        "settings",
        "Open Settings (also reachable from Search / Control Center)",
        legacy_flags=("--toggle-settings",),
        recommend_global_bind=False,
    ),
    ShellAction(
        "control-center",
        "Toggle full control center",
        legacy_flags=("--toggle-control-center",),
    ),
    ShellAction(
        "notifications",
        "Toggle notifications history panel",
        legacy_flags=("--toggle-notifications",),
    ),
    ShellAction(
        "session",
        "Toggle session / power menu",
        legacy_flags=("--toggle-session",),
    ),
    ShellAction(
        "tasks",
        "Open tasks panel (open-only; not a toggle)",
        legacy_flags=("--open-tasks",),
        recommend_global_bind=False,
    ),
    ShellAction(
        "reload-theme",
        "Reload active theme CSS",
        legacy_flags=("--reload-theme",),
        recommend_global_bind=False,
    ),
)

_BY_NAME = {action.name: action for action in ACTIONS}
_BY_FLAG = {
    flag: action.name
    for action in ACTIONS
    for flag in action.legacy_flags
}

# Known but intentionally unsupported — callers get a clear error, not a silent no-op.
UNSUPPORTED: dict[str, str] = {
    "window-switcher": (
        "Jugoo has no window-switcher overlay. Use Hyprland "
        "(e.g. ALT+Tab / cyclenext) instead of a Jugoo action."
    ),
}

_UNSUPPORTED_FLAGS = {
    "--toggle-window-switcher": "window-switcher",
}


def known_action_names() -> tuple[str, ...]:
    return tuple(action.name for action in ACTIONS)


def resolve_actions_from_argv(arguments: Sequence[str]) -> tuple[str, ...]:
    """Return ordered unique action names requested by ``argv`` (without prog name).

    Accepts::

        action launcher
        action launcher clipboard
        --toggle-launcher
        --list-actions   (handled by caller; not returned here)
    """
    args = [str(item) for item in arguments]
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        names.append(name)

    i = 0
    while i < len(args):
        token = args[i]
        if token in {"action", "toggle"}:
            i += 1
            while i < len(args) and not args[i].startswith("-"):
                add(args[i])
                i += 1
            continue
        if token in _BY_FLAG:
            add(_BY_FLAG[token])
            i += 1
            continue
        if token in _UNSUPPORTED_FLAGS:
            add(_UNSUPPORTED_FLAGS[token])
            i += 1
            continue
        i += 1
    return tuple(names)


def format_actions_help() -> str:
    lines = ["Jugoo actions (jugoo action <name>):", ""]
    for action in ACTIONS:
        bind = "bind OK" if action.recommend_global_bind else "prefer in-app"
        flags = ", ".join(action.legacy_flags) if action.legacy_flags else "—"
        lines.append(f"  {action.name:<16} {action.description}")
        lines.append(f"  {'':16} legacy: {flags}  [{bind}]")
    lines.append("")
    lines.append("Unsupported (do not bind to Jugoo):")
    for name, reason in UNSUPPORTED.items():
        lines.append(f"  {name:<16} {reason}")
    return "\n".join(lines)


def dispatch_action(name: str, shell) -> str | None:
    """Run ``name`` on ``shell`` (ShellApplication). Return error message or None."""
    if name in UNSUPPORTED:
        return UNSUPPORTED[name]
    action = _BY_NAME.get(name)
    if action is None:
        return f"unknown action {name!r}; try: jugoo action list"

    handlers: dict[str, Callable[[], None]] = {
        "launcher": shell.toggle_launcher,
        "clipboard": shell.toggle_clipboard_picker,
        "emoji": shell.toggle_emoji_picker,
        "playStopMusic": shell.media_service.play_pause_player,
        "media": shell.toggle_media_popup,
        "settings": shell.toggle_settings,
        "control-center": shell.toggle_control_center,
        "notifications": shell.toggle_notifications,
        "session": shell.toggle_session,
        "tasks": shell.open_tasks_panel,
        "reload-theme": shell.reload_theme,
    }
    handler = handlers.get(name)
    if handler is None:
        return f"action {name!r} is not wired"
    handler()
    return None
