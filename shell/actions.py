"""External action surface for a running Jugoo shell.

Hyprland (or any client) invokes the unique Gio.Application ``com.jugoo.Shell``.
The primary instance dispatches into existing ``ShellApplication`` methods — no
second GTK shell, no parallel EventBus.

Preferred CLI::

    jugoo action launcher
    jugoo action list
    jugoo action bluetooth-connect AA:BB:CC:DD:EE:FF

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
    # When True, the next CLI token is treated as an address/path argument.
    takes_target: bool = False


@dataclass(frozen=True, slots=True)
class ActionInvocation:
    """Resolved action plus optional target argument (e.g. Bluetooth address)."""

    name: str
    args: tuple[str, ...] = ()


# Canonical action ids — keep this list small on purpose.
ACTIONS: tuple[ShellAction, ...] = (
    ShellAction(
        "ask",
        "Open AI prompt under the bar (Enter sends, Escape cancels)",
        legacy_flags=("--toggle-ask",),
    ),
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
        "musicVolumeUp",
        "Raise Strawberry player volume",
    ),
    ShellAction(
        "musicVolumeDown",
        "Lower Strawberry player volume",
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
        "dismiss-popups-outside",
        "Close shell popups if the pointer is outside them (Hyprland mouse-release hook)",
        recommend_global_bind=False,
    ),
    ShellAction(
        "reload-theme",
        "Reload active theme CSS",
        legacy_flags=("--reload-theme",),
        recommend_global_bind=False,
    ),
    ShellAction(
        "sddm-apply",
        "Apply Jugoo SDDM theme from settings (Polkit)",
        recommend_global_bind=False,
    ),
    ShellAction(
        "sddm-restore",
        "Restore previous SDDM theme selection (Polkit)",
        recommend_global_bind=False,
    ),
    ShellAction(
        "bluetooth-toggle",
        "Toggle Bluetooth adapter power",
    ),
    ShellAction(
        "bluetooth-scan",
        "Start Bluetooth device discovery",
    ),
    ShellAction(
        "bluetooth-connect",
        "Connect to a Bluetooth device (requires address)",
        takes_target=True,
        recommend_global_bind=False,
    ),
    ShellAction(
        "bluetooth-disconnect",
        "Disconnect a Bluetooth device (requires address)",
        takes_target=True,
        recommend_global_bind=False,
    ),
    ShellAction(
        "bluetooth-pair",
        "Pair a Bluetooth device (requires address)",
        takes_target=True,
        recommend_global_bind=False,
    ),
    ShellAction(
        "bluetooth-remove",
        "Forget / remove a paired Bluetooth device (requires address)",
        takes_target=True,
        recommend_global_bind=False,
    ),
)

_BY_NAME = {action.name: action for action in ACTIONS}
_BY_FLAG = {
    flag: action.name
    for action in ACTIONS
    for flag in action.legacy_flags
}
_TARGET_ACTIONS = {action.name for action in ACTIONS if action.takes_target}

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


def resolve_action_calls(arguments: Sequence[str]) -> tuple[ActionInvocation, ...]:
    """Return ordered unique action invocations from ``argv`` (without prog name).

    Accepts::

        action launcher
        action launcher clipboard
        action bluetooth-connect AA:BB:CC:DD:EE:FF
        --toggle-launcher
    """
    args = [str(item) for item in arguments]
    calls: list[ActionInvocation] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def add(name: str, call_args: tuple[str, ...] = ()) -> None:
        key = (name, call_args)
        if key in seen:
            return
        seen.add(key)
        calls.append(ActionInvocation(name, call_args))

    i = 0
    while i < len(args):
        token = args[i]
        if token in {"action", "toggle"}:
            i += 1
            while i < len(args) and not args[i].startswith("-"):
                name = args[i]
                i += 1
                call_args: tuple[str, ...] = ()
                if name in _TARGET_ACTIONS and i < len(args) and not args[i].startswith("-"):
                    call_args = (args[i],)
                    i += 1
                add(name, call_args)
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
    return tuple(calls)


def resolve_actions_from_argv(arguments: Sequence[str]) -> tuple[str, ...]:
    """Return ordered unique action names (compatibility helper)."""
    return tuple(call.name for call in resolve_action_calls(arguments))


def format_actions_help() -> str:
    lines = ["Jugoo actions (jugoo action <name>):", ""]
    for action in ACTIONS:
        bind = "bind OK" if action.recommend_global_bind else "prefer in-app"
        flags = ", ".join(action.legacy_flags) if action.legacy_flags else "—"
        label = f"{action.name} <address>" if action.takes_target else action.name
        lines.append(f"  {label:<28} {action.description}")
        lines.append(f"  {'':28} legacy: {flags}  [{bind}]")
    lines.append("")
    lines.append("Unsupported (do not bind to Jugoo):")
    for name, reason in UNSUPPORTED.items():
        lines.append(f"  {name:<28} {reason}")
    return "\n".join(lines)


def dispatch_action(
    name: str,
    shell,
    args: Sequence[str] = (),
) -> str | None:
    """Run ``name`` on ``shell`` (ShellApplication). Return error message or None."""
    if name in UNSUPPORTED:
        return UNSUPPORTED[name]
    action = _BY_NAME.get(name)
    if action is None:
        return f"unknown action {name!r}; try: jugoo action list"

    if action.takes_target and not args:
        return f"action {name!r} requires a device address"

    handlers: dict[str, Callable[[], None]] = {
        "ask": lambda: shell.toggle_ai_prompt(),
        "launcher": lambda: shell.toggle_launcher(),
        "clipboard": lambda: shell.toggle_clipboard_picker(),
        "emoji": lambda: shell.toggle_emoji_picker(),
        "playStopMusic": lambda: shell.media_service.play_pause_player(),
        "musicVolumeUp": lambda: shell.media_service.volume_up_player(),
        "musicVolumeDown": lambda: shell.media_service.volume_down_player(),
        "media": lambda: shell.toggle_media_popup(),
        "settings": lambda: shell.toggle_settings(),
        "control-center": lambda: shell.toggle_control_center(),
        "notifications": lambda: shell.toggle_notifications(),
        "session": lambda: shell.toggle_session(),
        "tasks": lambda: shell.open_tasks_panel(),
        "dismiss-popups-outside": lambda: shell.dismiss_popups_outside(),
        "reload-theme": lambda: shell.reload_theme(),
        "sddm-apply": lambda: shell.apply_sddm_theme(),
        "sddm-restore": lambda: shell.restore_sddm_theme(),
        "bluetooth-toggle": lambda: shell.bluetooth_toggle(),
        "bluetooth-scan": lambda: shell.bluetooth_scan(),
        "bluetooth-connect": lambda: shell.bluetooth_connect(args[0]),
        "bluetooth-disconnect": lambda: shell.bluetooth_disconnect(args[0]),
        "bluetooth-pair": lambda: shell.bluetooth_pair(args[0]),
        "bluetooth-remove": lambda: shell.bluetooth_remove(args[0]),
    }
    handler = handlers.get(name)
    if handler is None:
        return f"action {name!r} is not wired"
    handler()
    return None


def dispatch_invocation(call: ActionInvocation, shell) -> str | None:
    return dispatch_action(call.name, shell, call.args)
