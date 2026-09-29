"""Inspect how a launcher application is installed without changing its catalog.

The launcher already discovers applications from FreeDesktop entries.  This
module enriches one of those entries only when the user asks for its details;
it must not become a second application-discovery mechanism.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
import os
import shlex
import shutil
import subprocess

from ...models import DesktopApplication
from .desktop import strip_exec_field_codes


@dataclass(frozen=True)
class ApplicationInspection:
    """User-facing, evidence-based details about one desktop application."""

    name: str
    origin: str
    package: str = ""
    locations: tuple[tuple[str, str], ...] = ()
    desktop_exec: str = ""
    launch_command: str = ""


CommandRunner = Callable[[Sequence[str]], str | None]


def inspect_application(
    application: DesktopApplication,
    *,
    runner: CommandRunner | None = None,
) -> ApplicationInspection:
    """Return details supported by desktop, Flatpak, or pacman metadata.

    A package is never guessed from an application name.  ``pacman -Qo`` is
    used to establish ownership of an actual executable or desktop entry.
    """
    run = runner or _run_command
    raw_exec = application.desktop_exec.strip() or application.exec_cmd.strip()
    launch_command = application.exec_cmd.strip() or strip_exec_field_codes(raw_exec)
    locations = _unique_locations(
        (("Desktop Entry", application.desktop_path),) if application.desktop_path else ()
    )

    flatpak_id = _flatpak_id(application, raw_exec)
    if flatpak_id:
        flatpak_location = run(("flatpak", "info", "--show-location", flatpak_id))
        if flatpak_location:
            locations = _unique_locations(
                locations + (("Instalación Flatpak", flatpak_location.strip()),)
            )
        return ApplicationInspection(
            name=application.name,
            origin="Flatpak",
            package=flatpak_id,
            locations=locations,
            desktop_exec=raw_exec,
            launch_command=launch_command or f"flatpak run {flatpak_id}",
        )

    executable = _resolve_executable(launch_command)
    appimage = _appimage_path(launch_command, executable)
    if appimage:
        return ApplicationInspection(
            name=application.name,
            origin="AppImage",
            locations=_unique_locations(locations + (("AppImage", appimage),)),
            desktop_exec=raw_exec,
            launch_command=launch_command,
        )

    owned_package = _pacman_owner(run, executable) or _pacman_owner(run, application.desktop_path)
    if owned_package:
        # ``pacman -Qm`` identifies a foreign/local package, but that alone
        # cannot prove it came from AUR; do not label it AUR without proof.
        foreign = run(("pacman", "-Qm", owned_package)) is not None
        origin = "Pacman (paquete externo)" if foreign else "Pacman"
        extra = (("Ejecutable", executable),) if executable else ()
        return ApplicationInspection(
            name=application.name,
            origin=origin,
            package=owned_package,
            locations=_unique_locations(locations + extra),
            desktop_exec=raw_exec,
            launch_command=launch_command,
        )

    if executable:
        return ApplicationInspection(
            name=application.name,
            origin="Aplicación/binario manual",
            locations=_unique_locations(locations + (("Ejecutable", executable),)),
            desktop_exec=raw_exec,
            launch_command=launch_command,
        )
    if application.desktop_path:
        return ApplicationInspection(
            name=application.name,
            origin="Archivo .desktop",
            locations=locations,
            desktop_exec=raw_exec,
            launch_command=launch_command,
        )
    return ApplicationInspection(
        name=application.name,
        origin="Desconocido",
        desktop_exec=raw_exec,
        launch_command=launch_command,
    )


def _flatpak_id(application: DesktopApplication, command: str) -> str:
    """Read a Flatpak application id from authoritative launcher evidence."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    for index, token in enumerate(tokens):
        if Path(token).name != "flatpak" or index + 1 >= len(tokens):
            continue
        try:
            run_index = tokens.index("run", index + 1)
        except ValueError:
            continue
        candidates = _flatpak_run_candidates(tokens[run_index + 1 :])
        return next(candidates, "")
    desktop = Path(application.desktop_path)
    if "flatpak/exports/share/applications" in str(desktop) and application.id:
        return application.id
    return ""


def _flatpak_run_candidates(tokens: Sequence[str]) -> Iterator[str]:
    """Yield positional Flatpak-run arguments, skipping option values."""
    options_with_value = {"--arch", "--branch", "--command", "--cwd", "--env"}
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in options_with_value:
            skip_next = True
            continue
        if token.startswith("-") or token == "@@":
            continue
        yield token


def _resolve_executable(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens:
        return ""
    executable = tokens[0]
    if os.path.isabs(executable) and Path(executable).is_file():
        return executable
    found = shutil.which(executable)
    return found or ""


def _appimage_path(command: str, executable: str) -> str:
    candidates = [executable]
    try:
        candidates.extend(shlex.split(command))
    except ValueError:
        pass
    for candidate in candidates:
        if candidate and candidate.casefold().endswith(".appimage") and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return ""


def _pacman_owner(run: CommandRunner, path: str) -> str:
    if not path:
        return ""
    output = run(("pacman", "-Qo", path))
    if not output:
        return ""
    # Arch emits: ``/usr/bin/foo is owned by package-name version``.
    marker = " is owned by "
    line = output.strip().splitlines()[0] if output.strip() else ""
    if marker not in line:
        return ""
    owner = line.split(marker, 1)[1].split(maxsplit=1)[0]
    return owner.strip()


def _unique_locations(locations: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in locations:
        if item[1] and item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)


def _run_command(command: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None
