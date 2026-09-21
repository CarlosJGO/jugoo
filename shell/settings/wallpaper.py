"""Desktop wallpaper via swaybg (preferred) or hyprpaper — applied from Settings."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..runtime_paths import wallpaper_dir, xdg_runtime_dir

BackendName = str
FillMode = str

ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
SWAYBG_MODES = {"crop": "fill", "fit": "fit", "stretch": "stretch"}
FILL_CHOICES = ("crop", "fit", "stretch")

Which = Callable[[str], str | None]
PopenFactory = Callable[..., subprocess.Popen]
HyprctlRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class WallpaperStatus:
    path: str
    fill: str
    backend: BackendName | None
    active: bool
    message: str


class WallpaperService:
    """Copy the chosen image into XDG data and keep one wallpaper process running."""

    def __init__(
        self,
        *,
        dest_dir: Path | None = None,
        pid_file: Path | None = None,
        which: Which | None = None,
        popen: PopenFactory | None = None,
        hyprctl: HyprctlRunner | None = None,
        on_status: Callable[[WallpaperStatus], None] | None = None,
    ) -> None:
        self._dest_dir = dest_dir or wallpaper_dir()
        self._pid_file = pid_file or (xdg_runtime_dir() / "wallpaper.pid")
        self._which = which or shutil.which
        self._popen = popen or subprocess.Popen
        self._hyprctl = hyprctl or _default_hyprctl
        self._on_status = on_status
        self._process: subprocess.Popen | None = None
        self._path = ""
        self._fill: FillMode = "crop"
        self._backend = detect_backend(self._which)
        self._status = WallpaperStatus(
            path="",
            fill="crop",
            backend=self._backend,
            active=False,
            message="Sin fondo personalizado.",
        )

    @property
    def backend(self) -> BackendName | None:
        return self._backend

    @property
    def status(self) -> WallpaperStatus:
        return self._status

    def configure(self, *, path: str, fill: str) -> WallpaperStatus:
        self._path = str(path or "").strip()
        fill_name = str(fill or "crop").strip()
        self._fill = fill_name if fill_name in FILL_CHOICES else "crop"
        return self.apply()

    def apply(self) -> WallpaperStatus:
        if not self._path:
            ok, message = self._clear()
            return self._emit(active=False, message=message if ok else message)

        image = self._resolve_image(self._path)
        if image is None:
            return self._emit(
                active=False,
                message=f"No se encontró la imagen: {self._path}",
            )

        try:
            installed = install_wallpaper(image, self._dest_dir)
        except (OSError, ValueError) as error:
            return self._emit(active=False, message=f"No se pudo copiar el fondo: {error}")

        if self._backend is None:
            self._backend = detect_backend(self._which)
        if self._backend is None:
            return self._emit(
                active=False,
                message=(
                    "No hay herramienta de fondo. Instala swaybg "
                    "(recomendado) o hyprpaper."
                ),
            )

        self._stop_owned()
        if self._backend == "swaybg":
            ok, message = self._start_swaybg(installed)
        else:
            ok, message = self._start_hyprpaper(installed)
        if ok:
            self._prepare_hyprland_surface()
        return self._emit(active=ok, message=message)

    def close(self) -> None:
        """Keep the wallpaper process; only drop the in-memory handle."""
        self._process = None

    def _clear(self) -> tuple[bool, str]:
        self._stop_owned()
        clear_installed_wallpapers(self._dest_dir)
        return True, "Sin fondo personalizado."

    def _resolve_image(self, raw: str) -> Path | None:
        path = Path(raw).expanduser()
        if path.is_file():
            return path
        installed = current_installed_wallpaper(self._dest_dir)
        if installed is not None:
            return installed
        return None

    def _start_swaybg(self, image: Path) -> tuple[bool, str]:
        mode = SWAYBG_MODES.get(self._fill, "fill")
        command = ["swaybg", "-i", str(image), "-m", mode]
        try:
            process = self._popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            return False, f"No se pudo iniciar swaybg: {error}"
        self._remember(process)
        return True, f"swaybg ({mode}) → {image.name}"

    def _start_hyprpaper(self, image: Path) -> tuple[bool, str]:
        if not self._hyprpaper_is_alive():
            try:
                process = self._popen(
                    ["hyprpaper"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as error:
                return False, f"No se pudo iniciar hyprpaper: {error}"
            self._remember(process)
        preload = self._hyprctl(["hyprpaper", "preload", str(image)])
        wallpaper = self._hyprctl(["hyprpaper", "wallpaper", f",{image}"])
        if wallpaper.returncode != 0:
            detail = (wallpaper.stderr or wallpaper.stdout or "").strip()
            return False, detail or "hyprpaper no aplicó el fondo"
        if preload.returncode != 0:
            # Preload can fail when the image is already cached; wallpaper succeeded.
            return True, f"hyprpaper → {image.name}"
        return True, f"hyprpaper → {image.name}"

    def _hyprpaper_is_alive(self) -> bool:
        probe = self._hyprctl(["hyprpaper", "listloaded"])
        return probe.returncode == 0

    def _prepare_hyprland_surface(self) -> None:
        self._hyprctl(["keyword", "misc:disable_hyprland_logo", "true"])
        self._hyprctl(["keyword", "misc:force_default_wallpaper", "0"])

    def _remember(self, process: subprocess.Popen) -> None:
        self._process = process
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(f"{pid}\n", encoding="utf-8")
        except OSError:
            pass

    def _stop_owned(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            _terminate(process)
        pid = _read_pid(self._pid_file)
        if pid is not None and (process is None or pid != getattr(process, "pid", None)):
            _kill_pid(pid)
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _emit(self, *, active: bool, message: str) -> WallpaperStatus:
        self._status = WallpaperStatus(
            path=self._path,
            fill=self._fill,
            backend=self._backend,
            active=active,
            message=message,
        )
        if self._on_status is not None:
            self._on_status(self._status)
        return self._status


def detect_backend(which: Which | None = None) -> BackendName | None:
    finder = which or shutil.which
    for name in ("swaybg", "hyprpaper"):
        if finder(name):
            return name
    return None


def install_wallpaper(source: Path, dest_dir: Path) -> Path:
    """Copy ``source`` to ``dest_dir/current.<ext>``, replacing any previous file."""
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    suffix = source.suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported wallpaper type: {suffix or '(none)'}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"current{suffix}"
    if dest.resolve() != source:
        clear_installed_wallpapers(dest_dir)
        shutil.copy2(source, dest)
    return dest


def clear_installed_wallpapers(dest_dir: Path) -> None:
    if not dest_dir.is_dir():
        return
    for path in dest_dir.glob("current.*"):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def current_installed_wallpaper(dest_dir: Path) -> Path | None:
    if not dest_dir.is_dir():
        return None
    matches = sorted(
        path
        for path in dest_dir.glob("current.*")
        if path.is_file() and path.suffix.casefold() in ALLOWED_SUFFIXES
    )
    return matches[0] if matches else None


def _default_hyprctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["hyprctl", *args]
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))


def _read_pid(pid_file: Path) -> int | None:
    try:
        text = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if pid > 0 else None


def _kill_pid(pid: int) -> None:
    if not _pid_is_wallpaper(pid):
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


def _pid_is_wallpaper(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    text = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
    return "swaybg" in text or "hyprpaper" in text


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
