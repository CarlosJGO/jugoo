"""Sync user application shortcuts into a managed Hyprland conf block."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .hyprland import default_hyprland_conf_path
from .user_apps import UserAppShortcut

MARKER_BEGIN = "# >>> jugoo-app-shortcuts begin"
MARKER_END = "# >>> jugoo-app-shortcuts end"

_BLOCK_RE = re.compile(
    re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END),
    re.DOTALL,
)


def render_managed_block(shortcuts: tuple[UserAppShortcut, ...]) -> str:
    lines = [
        MARKER_BEGIN,
        "# Managed by Jugoo Configuraciones → Atajos. Do not edit by hand.",
    ]
    for item in shortcuts:
        lines.append(item.hypr_bind_line())
    lines.append(MARKER_END)
    return "\n".join(lines)


def sync_user_app_shortcuts_to_hypr(
    shortcuts: tuple[UserAppShortcut, ...],
    *,
    conf_path: Path | None = None,
    reload: bool = True,
) -> Path:
    """Replace/insert the managed bind block. Never executes bind commands."""
    path = conf_path if conf_path is not None else default_hyprland_conf_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""

    block = render_managed_block(shortcuts)
    if _BLOCK_RE.search(text):
        updated = _BLOCK_RE.sub(block, text, count=1)
    else:
        trimmed = text.rstrip()
        updated = (trimmed + "\n\n" + block + "\n") if trimmed else (block + "\n")

    if updated != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(path)

    if reload:
        reload_hyprland()
    return path


def reload_hyprland() -> bool:
    try:
        completed = subprocess.run(
            ["hyprctl", "reload"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
