"""CLI entry for SDDM apply/restore — runs in this process, not via Gio primary.

Privileged Polkit prompts must not block the GTK shell main loop. Terminal
invocations therefore bypass the running ``com.jugoo.Shell`` instance.
"""

from __future__ import annotations

import sys

from ...config import ACTIVE_THEME
from ...identity import project_root
from ...runtime_paths import settings_path
from ...settings.store import SettingsStore
from ...ui.theme import load_theme
from .service import SddmApplyResult, SddmService


def run_sddm_cli(action: str) -> int:
    """Handle ``jugoo action sddm-apply|sddm-restore`` in the foreground."""
    if action not in {"sddm-apply", "sddm-restore"}:
        print(f"jugoo: unknown SDDM action {action!r}", file=sys.stderr)
        return 2

    store = SettingsStore(settings_path())
    store.load()
    theme_name = str(store.get("tema.active") or ACTIVE_THEME)
    theme_path = project_root() / "themes" / f"{theme_name}.toml"
    if not theme_path.is_file():
        theme_path = project_root() / "themes" / "space.toml"

    service = SddmService(
        theme_provider=lambda: load_theme(theme_path),
    )

    if action == "sddm-restore":
        print("Jugoo SDDM: restaurando (Polkit puede pedir contraseña)…", flush=True)
        result = service.restore()
        if result.ok:
            store.set("sddm.enabled", False)
            store.save()
    else:
        print("Jugoo SDDM: aplicando (Polkit puede pedir contraseña)…", flush=True)
        settings = {key: store.get(key) for key in _SDDM_KEYS}
        result = service.apply(settings)

    _print_result(result)
    return 0 if result.ok else 1


_SDDM_KEYS = (
    "sddm.enabled",
    "sddm.background_path",
    "sddm.background_dim",
    "sddm.background_fill",
    "sddm.show_avatar",
    "sddm.show_clock",
    "sddm.form_opacity",
    "sddm.form_radius",
)


def _print_result(result: SddmApplyResult) -> None:
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream, flush=True)
    if result.effective_current:
        print(f"Tema SDDM efectivo: {result.effective_current}", flush=True)
    if not result.ok:
        print(
            "Si Polkit falló: comprueba la contraseña de administrador "
            "(usuario en grupo wheel) y vuelve a ejecutar "
            "`jugoo action sddm-apply`.",
            file=sys.stderr,
            flush=True,
        )
