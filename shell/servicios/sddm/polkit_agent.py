"""Ensure a Polkit authentication agent is available before ``pkexec``.

Hyprland does not ship a desktop agent. Without one, ``pkexec`` falls back to a
textual listener that, with polkit 127's socket-activated helper, often fails
with ``No session for cookie`` after the password prompt — even when the user
is in ``wheel`` and ``sudo`` works.

Preferred agent on this stack: ``hyprpolkitagent`` (Hyprland wiki "Must have").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HYPR_AGENT_BIN = Path("/usr/lib/hyprpolkitagent/hyprpolkitagent")
HYPR_AGENT_UNIT = "hyprpolkitagent.service"


@dataclass(frozen=True)
class PolkitAgentStatus:
    ok: bool
    kind: str  # hyprpolkitagent | none
    message: str


def ensure_polkit_agent() -> PolkitAgentStatus:
    """Start a graphical Polkit agent if needed. Never elevates privileges."""
    if _hypr_agent_running():
        return PolkitAgentStatus(
            ok=True,
            kind="hyprpolkitagent",
            message="hyprpolkitagent already running",
        )

    if HYPR_AGENT_BIN.is_file():
        started = _start_hypr_agent()
        if started and _wait_hypr_agent(timeout_sec=5.0):
            return PolkitAgentStatus(
                ok=True,
                kind="hyprpolkitagent",
                message="hyprpolkitagent started",
            )
        return PolkitAgentStatus(
            ok=False,
            kind="none",
            message=(
                "hyprpolkitagent está instalado pero no arrancó. "
                "Prueba: systemctl --user enable --now hyprpolkitagent.service"
            ),
        )

    return PolkitAgentStatus(
        ok=False,
        kind="none",
        message=(
            "Falta un authentication agent de Polkit en la sesión Hyprland. "
            "Sin él, pkexec falla con «Not authorized» / «No session for cookie» "
            "aunque sudo funcione. Instala e inicia el agente oficial:\n"
            "  sudo pacman -S hyprpolkitagent\n"
            "  systemctl --user enable --now hyprpolkitagent.service\n"
            "O añade a hyprland.conf: exec-once = systemctl --user start hyprpolkitagent"
        ),
    )


def _hypr_agent_running() -> bool:
    if _user_unit_active(HYPR_AGENT_UNIT):
        return True
    # Match the real binary only — avoid ``pgrep -f`` false positives from
    # shells/tests whose command line merely mentions the package name.
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-x", "hyprpolkitagent"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    if result.returncode == 0 and result.stdout.strip():
        return True
    # Some builds keep the path as the process name under /usr/lib/.../
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", f"^{HYPR_AGENT_BIN}([[:space:]]|$)"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _start_hypr_agent() -> bool:
    # Prefer the user unit shipped by the package.
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "start", HYPR_AGENT_UNIT],
            check=False,
            capture_output=True,
            text=True,
            env=_session_env(),
        )
        if result.returncode == 0:
            return True
    # Fallback: launch the binary in the background.
    try:
        subprocess.Popen(
            [str(HYPR_AGENT_BIN)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_session_env(),
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def _wait_hypr_agent(*, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _hypr_agent_running():
            return True
        time.sleep(0.15)
    return False


def _user_unit_active(unit: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        check=False,
        capture_output=True,
        text=True,
        env=_session_env(),
    )
    return result.returncode == 0 and result.stdout.strip() == "active"


def _session_env() -> dict[str, str]:
    """Preserve the graphical session bus for systemctl --user / the agent."""
    env = os.environ.copy()
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env
