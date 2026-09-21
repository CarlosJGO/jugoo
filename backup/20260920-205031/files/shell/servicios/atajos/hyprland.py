"""Safe, read-only Hyprland bind discovery.

Primary source: ``hyprctl -j binds`` (active runtime binds).
Fallback: parse ``hyprland.conf`` text without executing any command text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .keys import format_conf_keys, format_modmask_keys
from .labels import classify_bind
from .model import ShortcutEntry

JsonRunner = Callable[[str], Any]
# Matches classic Hyprland conf binds. Captures mods, key, dispatcher, optional arg.
_CONF_BIND_RE = re.compile(
    r"^\s*(?P<kind>bind[lrems]*)\s*=\s*"
    r"(?P<mods>[^,]*),\s*"
    r"(?P<key>[^,]+),\s*"
    r"(?P<dispatcher>[^,]+)"
    r"(?:,\s*(?P<arg>.*))?$"
)


@dataclass(frozen=True, slots=True)
class RawBind:
    """Intermediate bind before friendly labeling."""

    modmask: int
    mods_text: str
    key: str
    dispatcher: str
    arg: str
    submap: str = ""
    mouse: bool = False
    origin: str = "hyprctl"


def default_hyprland_conf_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "hypr" / "hyprland.conf"


def fetch_hyprctl_binds_json(
    *,
    runner: JsonRunner | None = None,
) -> list[dict[str, Any]] | None:
    """Return active binds from Hyprland, or ``None`` if unavailable.

    Never interprets ``arg`` as a shell command.
    """
    if runner is not None:
        try:
            payload = runner("binds")
        except Exception:  # noqa: BLE001 — callers expect soft failure
            return None
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return None

    try:
        completed = subprocess.run(
            ["hyprctl", "-j", "binds"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        payload = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return None
    if not isinstance(payload, list):
        return None
    return [item for item in payload if isinstance(item, dict)]


def parse_hyprctl_binds(payload: list[dict[str, Any]]) -> tuple[RawBind, ...]:
    binds: list[RawBind] = []
    for item in payload:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        dispatcher = str(item.get("dispatcher") or "").strip()
        if not dispatcher:
            continue
        mouse = bool(item.get("mouse"))
        binds.append(
            RawBind(
                modmask=int(item.get("modmask") or 0),
                mods_text="",
                key=key,
                dispatcher=dispatcher,
                arg=str(item.get("arg") or ""),
                submap=str(item.get("submap") or ""),
                mouse=mouse,
                origin="hyprctl",
            )
        )
    return tuple(binds)


def parse_hyprland_conf_text(text: str) -> tuple[RawBind, ...]:
    """Parse ``bind =`` lines from conf text. Informational only."""
    binds: list[RawBind] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _CONF_BIND_RE.match(stripped)
        if match is None:
            continue
        kind = match.group("kind").casefold()
        mods = (match.group("mods") or "").strip()
        key = (match.group("key") or "").strip()
        dispatcher = (match.group("dispatcher") or "").strip()
        arg = (match.group("arg") or "").rstrip().rstrip(",").strip()
        if not key or not dispatcher:
            continue
        mouse = kind.endswith("m") or key.casefold().startswith("mouse:")
        binds.append(
            RawBind(
                modmask=0,
                mods_text=mods,
                key=key,
                dispatcher=dispatcher,
                arg=arg,
                mouse=mouse,
                origin="conf",
            )
        )
    return tuple(binds)


def load_conf_binds(path: Path | None = None) -> tuple[RawBind, ...]:
    conf_path = path if path is not None else default_hyprland_conf_path()
    try:
        text = conf_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return parse_hyprland_conf_text(text)


def raw_bind_to_entry(bind: RawBind, *, index: int) -> ShortcutEntry | None:
    """Convert a raw bind into a display entry. Skips mouse binds by default."""
    if bind.mouse:
        return None
    if bind.mods_text:
        keys = format_conf_keys(bind.mods_text, bind.key)
    else:
        keys = format_modmask_keys(bind.modmask, bind.key)

    description, category, source, action = classify_bind(bind.dispatcher, bind.arg)
    entry_id = _make_id(keys, bind.dispatcher, bind.arg, bind.submap, index)
    return ShortcutEntry(
        id=entry_id,
        keys=keys,
        description=description,
        category=category,
        source=source,
        action=action,
        raw_dispatcher=bind.dispatcher,
        raw_arg=_sanitize_arg(bind.arg),
        submap=bind.submap,
        editable=False,
    )


def _make_id(keys: str, dispatcher: str, arg: str, submap: str, index: int) -> str:
    compact_keys = keys.replace(" ", "")
    compact_arg = re.sub(r"\s+", "_", (arg or "")[:48])
    base = f"{compact_keys}:{dispatcher}:{compact_arg}"
    if submap:
        base = f"{submap}/{base}"
    # Index keeps identical multi-binds unique without hashing command text.
    return f"bind:{index}:{base}"


def _sanitize_arg(arg: str) -> str:
    """Keep a short opaque copy for diagnostics; never treat as executable."""
    cleaned = " ".join((arg or "").split())
    if len(cleaned) > 120:
        return cleaned[:119] + "…"
    return cleaned
