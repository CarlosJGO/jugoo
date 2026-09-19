"""User-space SDDM orchestration: stage theme, invoke Polkit helper."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ...identity import assets_dir, project_root
from ...runtime_paths import xdg_cache_dir
from ...ui.theme import Theme, active_theme, load_theme
from .assets import clear_user_backgrounds, install_background
from .conf import SddmThemeOptions, render_theme_conf
from .effective import read_effective_current
from .polkit_agent import ensure_polkit_agent

THEME_ID = "jugoo"
SYSTEM_HELPER = Path("/usr/lib/jugoo/jugoo-sddm-helper")
SYSTEM_POLICY = Path("/usr/share/polkit-1/actions/com.jugoo.sddm.policy")
DROPIN_NAME = "10-jugoo.conf"

PkexecRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]

# Serialize privileged ops; never run pkexec on the GTK main thread.
_operation_lock = threading.Lock()


@dataclass(frozen=True)
class SddmStatus:
    enabled: bool
    effective_current: str
    theme_installed: bool
    helper_installed: bool
    managed: bool
    message: str


@dataclass(frozen=True)
class SddmApplyResult:
    ok: bool
    message: str
    effective_current: str = ""


DoneCallback = Callable[[SddmApplyResult], None]


class SddmService:
    """Prepare a Jugoo SDDM theme staging tree and apply/restore via Polkit."""

    def __init__(
        self,
        *,
        theme_source: Path | None = None,
        staging_root: Path | None = None,
        helper_source: Path | None = None,
        policy_source: Path | None = None,
        pkexec_runner: PkexecRunner | None = None,
        theme_provider: Callable[[], Theme | None] | None = None,
    ) -> None:
        root = project_root()
        self._theme_source = theme_source or (assets_dir() / "sddm" / "jugoo")
        self._staging_root = staging_root or (xdg_cache_dir() / "sddm-stage")
        self._helper_source = helper_source or (
            root / "shell" / "helpers" / "jugoo-sddm-helper"
        )
        self._policy_source = policy_source or (
            root / "packaging" / "polkit" / "com.jugoo.sddm.policy"
        )
        self._pkexec = pkexec_runner or _default_pkexec
        self._theme_provider = theme_provider or active_theme

    @property
    def staging_theme_dir(self) -> Path:
        return self._staging_root / THEME_ID

    def status(self, settings: Mapping[str, Any] | None = None) -> SddmStatus:
        enabled = bool((settings or {}).get("sddm.enabled", False))
        effective = read_effective_current()
        installed = (Path("/usr/share/sddm/themes") / THEME_ID / "Main.qml").is_file()
        helper = SYSTEM_HELPER.is_file()
        managed = effective == THEME_ID and (
            Path("/etc/sddm.conf.d") / DROPIN_NAME
        ).is_file()
        if managed:
            message = "Jugoo administra el tema SDDM activo."
        elif enabled:
            message = "SDDM marcado para administrar; pulsa Aplicar para activarlo."
        else:
            message = f"Tema SDDM efectivo: {effective or '(predeterminado)'}."
        return SddmStatus(
            enabled=enabled,
            effective_current=effective,
            theme_installed=installed,
            helper_installed=helper,
            managed=managed,
            message=message,
        )

    def build_staging(self, settings: Mapping[str, Any]) -> Path:
        """Build ``staging/jugoo`` from the bundled template + settings. User-only."""
        theme = self._resolve_theme()
        source = self._theme_source
        if not (source / "Main.qml").is_file() or not (source / "metadata.desktop").is_file():
            raise FileNotFoundError(f"missing SDDM theme template under {source}")

        staging = self.staging_theme_dir
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(
            source,
            staging,
            ignore=shutil.ignore_patterns("theme.conf", "Backgrounds", "__pycache__"),
        )
        backgrounds = staging / "Backgrounds"
        backgrounds.mkdir(parents=True, exist_ok=True)
        clear_user_backgrounds(backgrounds)

        background_rel = ""
        raw_bg = str(settings.get("sddm.background_path") or "").strip()
        if raw_bg:
            background_rel = install_background(Path(raw_bg), backgrounds)

        radius_setting = settings.get("sddm.form_radius")
        if radius_setting is None or radius_setting == "":
            form_radius = theme.shape.radius
        else:
            form_radius = int(radius_setting)

        options = SddmThemeOptions(
            background=background_rel,
            background_dim=float(settings.get("sddm.background_dim", 0.45)),
            background_fill=str(settings.get("sddm.background_fill") or "crop"),
            show_avatar=bool(settings.get("sddm.show_avatar", True)),
            show_clock=bool(settings.get("sddm.show_clock", True)),
            form_opacity=float(settings.get("sddm.form_opacity", 0.88)),
            form_radius=form_radius,
        )
        (staging / "theme.conf").write_text(
            render_theme_conf(theme, options),
            encoding="utf-8",
        )
        return staging

    def apply(self, settings: Mapping[str, Any]) -> SddmApplyResult:
        """Apply or deactivate according to ``sddm.enabled`` (explicit action)."""
        enabled = bool(settings.get("sddm.enabled", False))
        try:
            self._ensure_helper()
            if not enabled:
                return self._run_helper(["restore"])
            staging = self.build_staging(settings)
            return self._run_helper(["apply", str(staging)])
        except Exception as error:  # noqa: BLE001 — surface to UI/CLI
            return SddmApplyResult(ok=False, message=str(error))

    def restore(self) -> SddmApplyResult:
        try:
            self._ensure_helper()
            return self._run_helper(["restore"])
        except Exception as error:  # noqa: BLE001
            return SddmApplyResult(ok=False, message=str(error))

    def apply_async(
        self,
        settings: Mapping[str, Any],
        on_done: DoneCallback,
    ) -> bool:
        """Run ``apply`` off the caller thread. Returns False if already busy."""
        snapshot = dict(settings)
        return self._spawn("apply", lambda: self.apply(snapshot), on_done)

    def restore_async(self, on_done: DoneCallback) -> bool:
        """Run ``restore`` off the caller thread. Returns False if already busy."""
        return self._spawn("restore", self.restore, on_done)

    def _spawn(
        self,
        label: str,
        work: Callable[[], SddmApplyResult],
        on_done: DoneCallback,
    ) -> bool:
        if not _operation_lock.acquire(blocking=False):
            on_done(
                SddmApplyResult(
                    ok=False,
                    message="Ya hay una operación SDDM en curso (¿diálogo de Polkit abierto?).",
                )
            )
            return False

        def runner() -> None:
            try:
                result = work()
            except Exception as error:  # noqa: BLE001
                result = SddmApplyResult(ok=False, message=str(error))
            finally:
                _operation_lock.release()
            on_done(result)

        threading.Thread(
            target=runner,
            name=f"jugoo-sddm-{label}",
            daemon=True,
        ).start()
        return True

    def settings_snapshot(self, getter: Callable[[str], Any]) -> dict[str, Any]:
        keys = (
            "sddm.enabled",
            "sddm.background_path",
            "sddm.background_dim",
            "sddm.background_fill",
            "sddm.show_avatar",
            "sddm.show_clock",
            "sddm.form_opacity",
            "sddm.form_radius",
        )
        return {key: getter(key) for key in keys}

    def _resolve_theme(self) -> Theme:
        theme = self._theme_provider()
        if theme is not None:
            return theme
        fallback = project_root() / "themes" / "space.toml"
        return load_theme(fallback)

    def _ensure_helper(self) -> None:
        if SYSTEM_HELPER.is_file() and SYSTEM_POLICY.is_file():
            return
        if not self._helper_source.is_file():
            raise FileNotFoundError(f"helper missing: {self._helper_source}")
        if not self._policy_source.is_file():
            raise FileNotFoundError(f"polkit policy missing: {self._policy_source}")
        result = self._pkexec(
            [
                str(self._helper_source),
                "install-helper",
                str(self._helper_source),
                str(self._policy_source),
            ]
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(detail or "failed to install jugoo-sddm-helper")

    def _run_helper(self, args: list[str]) -> SddmApplyResult:
        helper = SYSTEM_HELPER if SYSTEM_HELPER.is_file() else self._helper_source
        result = self._pkexec([str(helper), *args])
        payload = _parse_helper_json(result.stdout)
        if payload:
            message = str(payload.get("message") or "")
            effective = str(payload.get("effective_current") or "")
            ok = bool(payload.get("ok", result.returncode == 0))
            return SddmApplyResult(
                ok=ok,
                message=message or ("ok" if ok else "failed"),
                effective_current=effective,
            )
        detail = (result.stderr or result.stdout or "").strip()
        if result.returncode == 0:
            return SddmApplyResult(
                ok=True,
                message=detail or "ok",
                effective_current=read_effective_current(),
            )
        if not detail:
            detail = (
                "Polkit denegó la operación o se canceló la autenticación. "
                "Vuelve a intentar e introduce la contraseña de administrador."
            )
        return SddmApplyResult(ok=False, message=detail)


def _default_pkexec(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke pkexec after ensuring a session Polkit authentication agent.

    On Hyprland, a graphical agent (hyprpolkitagent) is required. Textual
    fallback often ends in polkit-agent-helper «No session for cookie».
    """
    import sys
    import tempfile

    agent = ensure_polkit_agent()
    if not agent.ok:
        return subprocess.CompletedProcess(
            ["pkexec", *argv],
            126,
            stdout="",
            stderr=agent.message,
        )

    env = os.environ.copy()
    use_tty = False
    try:
        use_tty = sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        use_tty = False

    if use_tty:
        fd, result_path = tempfile.mkstemp(prefix="jugoo-sddm-", suffix=".json")
        os.close(fd)
        env["JUGOO_SDDM_RESULT"] = result_path
        try:
            completed = subprocess.run(
                ["pkexec", *argv],
                check=False,
                text=True,
                env=env,
            )
            stdout = ""
            try:
                stdout = Path(result_path).read_text(encoding="utf-8")
            except OSError:
                stdout = ""
            stderr = ""
            if completed.returncode != 0 and not stdout.strip():
                stderr = (
                    "Polkit denegó la operación (contraseña incorrecta, "
                    "cancelada, o el authentication agent no completó la "
                    "sesión). Si ves «No session for cookie» en el journal, "
                    "asegura: systemctl --user enable --now hyprpolkitagent."
                )
            return subprocess.CompletedProcess(
                completed.args,
                completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        finally:
            try:
                os.unlink(result_path)
            except OSError:
                pass

    return subprocess.run(
        ["pkexec", *argv],
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def _parse_helper_json(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    # Helper prints a single JSON object on the last non-empty line.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
