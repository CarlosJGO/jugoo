"""Screen warm-light (night mode) via system tools — not a bar simulation."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

BackendName = str


@dataclass(frozen=True)
class NightModeStatus:
    enabled: bool
    temperature: int
    auto_schedule: bool
    start_hour: int
    end_hour: int
    backend: BackendName | None
    active_now: bool
    message: str


class NightModeService:
    """Drive hyprsunset (preferred) or gammastep/wlsunset/redshift."""

    def __init__(self, *, on_status: Callable[[NightModeStatus], None] | None = None) -> None:
        self._on_status = on_status
        self._enabled = False
        self._temperature = 4500
        self._auto_schedule = False
        self._start_hour = 21
        self._end_hour = 7
        self._process: subprocess.Popen[bytes] | None = None
        self._backend = detect_backend()

    @property
    def backend(self) -> BackendName | None:
        return self._backend

    def configure(
        self,
        *,
        enabled: bool,
        temperature: int,
        auto_schedule: bool,
        start_hour: int,
        end_hour: int,
    ) -> NightModeStatus:
        self._enabled = enabled
        self._temperature = max(1000, min(6500, int(temperature)))
        self._auto_schedule = auto_schedule
        self._start_hour = int(start_hour) % 24
        self._end_hour = int(end_hour) % 24
        return self.apply()

    def apply(self) -> NightModeStatus:
        should = self._should_be_active()
        if should:
            ok, message = self._activate()
        else:
            ok, message = self._deactivate()
        status = NightModeStatus(
            enabled=self._enabled,
            temperature=self._temperature,
            auto_schedule=self._auto_schedule,
            start_hour=self._start_hour,
            end_hour=self._end_hour,
            backend=self._backend,
            active_now=should and ok,
            message=message,
        )
        if self._on_status is not None:
            self._on_status(status)
        return status

    def refresh_schedule(self) -> NightModeStatus:
        """Called periodically when auto schedule is on."""
        return self.apply()

    def close(self) -> None:
        self._stop_process()
        if self._backend == "hyprsunset":
            _run(["hyprctl", "hyprsunset", "identity"], check=False)
        elif self._backend in {"gammastep", "redshift"}:
            _run([self._backend, "-x"], check=False)

    def _should_be_active(self) -> bool:
        if not self._enabled:
            return False
        if not self._auto_schedule:
            return True
        hour = datetime.now().hour
        start = self._start_hour
        end = self._end_hour
        if start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _activate(self) -> tuple[bool, str]:
        if self._backend is None:
            return False, (
                "No hay herramienta de luz cálida. Instala hyprsunset "
                "(recomendado) o gammastep/wlsunset/redshift."
            )
        if self._backend == "hyprsunset":
            return self._activate_hyprsunset()
        if self._backend == "wlsunset":
            return self._activate_wlsunset()
        return self._activate_gamma_tool(self._backend)

    def _deactivate(self) -> tuple[bool, str]:
        self._stop_process()
        if self._backend == "hyprsunset":
            result = _run(["hyprctl", "hyprsunset", "identity"], check=False)
            if result.returncode == 0:
                return True, "Modo noche desactivado"
            # Daemon may not be running; that is fine when disabled.
            return True, "Modo noche desactivado"
        if self._backend in {"gammastep", "redshift"}:
            _run([self._backend, "-x"], check=False)
            return True, "Modo noche desactivado"
        if self._backend == "wlsunset":
            return True, "Modo noche desactivado"
        return True, "Modo noche desactivado"

    def _activate_hyprsunset(self) -> tuple[bool, str]:
        # Prefer talking to an already-running hyprsunset daemon.
        probe = _run(
            ["hyprctl", "hyprsunset", "temperature", str(self._temperature)],
            check=False,
        )
        if probe.returncode == 0:
            return True, f"hyprsunset → {self._temperature} K"
        # Start a one-shot / daemon instance if hyprctl control is unavailable.
        self._stop_process()
        try:
            self._process = subprocess.Popen(
                ["hyprsunset", "-t", str(self._temperature)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            return False, f"No se pudo iniciar hyprsunset: {error}"
        return True, f"hyprsunset iniciado → {self._temperature} K"

    def _activate_wlsunset(self) -> tuple[bool, str]:
        self._stop_process()
        try:
            self._process = subprocess.Popen(
                ["wlsunset", "-T", "6500", "-t", str(self._temperature)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            return False, f"No se pudo iniciar wlsunset: {error}"
        return True, f"wlsunset → {self._temperature} K"

    def _activate_gamma_tool(self, binary: str) -> tuple[bool, str]:
        self._stop_process()
        try:
            self._process = subprocess.Popen(
                [binary, "-O", str(self._temperature)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            return False, f"No se pudo iniciar {binary}: {error}"
        return True, f"{binary} → {self._temperature} K"

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def detect_backend() -> BackendName | None:
    for name in ("hyprsunset", "gammastep", "wlsunset", "redshift"):
        if shutil.which(name):
            return name
    return None


def _run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))
