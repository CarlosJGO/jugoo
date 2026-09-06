"""Install and refresh Jugoo's XDG desktop identity without compiling anything."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

from .identity import (
    APPLICATION_COMMENT,
    APPLICATION_ID,
    APPLICATION_NAME,
    COMMAND_NAME,
    ICON_NAME,
    assets_dir,
    discover_logo,
    project_root,
)

_WRAPPER_HEADER = "# Managed by Jugoo identity installer. Do not edit.\n"
_HICOLOR_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
_TASK_WATCHER_SERVICE = "jugoo-task-watcher.service"


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def xdg_bin_home() -> Path:
    return Path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local" / "bin"))


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def desktop_file_path() -> Path:
    return xdg_data_home() / "applications" / f"{APPLICATION_ID}.desktop"


def wrapper_path() -> Path:
    return xdg_bin_home() / COMMAND_NAME


def task_watcher_service_path() -> Path:
    return xdg_config_home() / "systemd" / "user" / _TASK_WATCHER_SERVICE


def install_identity(*, assets: Path | None = None) -> int:
    """Write wrapper, .desktop, and icon. Safe to run repeatedly."""
    binary = _write_wrapper()
    logo = discover_logo(assets)
    icon_installed = _install_icon(logo) if logo is not None else False
    _write_desktop_file(binary, icon_installed)
    _write_task_watcher_service(binary)
    if _user_systemd_is_live():
        _enable_task_watcher_service()
    _refresh_caches()
    _print_summary(binary, logo, icon_installed)
    return 0


def uninstall_identity() -> int:
    """Remove files this installer created. Leaves user data (pins, history) alone."""
    if _user_systemd_is_live():
        _run_optional(["systemctl", "--user", "disable", "--now", _TASK_WATCHER_SERVICE])
    for path in (desktop_file_path(), wrapper_path(), task_watcher_service_path()):
        _remove_file(path)
    _remove_installed_icons()
    _refresh_caches()
    _run_optional(["systemctl", "--user", "daemon-reload"])
    print("Jugoo: desktop identity removed.")
    return 0


def _write_wrapper() -> Path:
    root = project_root()
    path = wrapper_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "#!/bin/sh\n"
        f"{_WRAPPER_HEADER}"
        f"export PYTHONPATH={_shell_quote(str(root))}"
        "${PYTHONPATH:+:$PYTHONPATH}\n"
        f"cd {_shell_quote(str(root))} || exit 1\n"
        'exec python3 -m shell "$@"\n'
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def _write_desktop_file(binary: Path, icon_installed: bool) -> Path:
    path = desktop_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APPLICATION_NAME}",
        f"Comment={APPLICATION_COMMENT}",
        f"Exec={_desktop_exec(binary)}",
        f"TryExec={binary}",
        "Terminal=false",
        "StartupNotify=false",
        f"StartupWMClass={APPLICATION_ID}",
        "Categories=System;Utility;",
        "Keywords=shell;desktop;hyprland;wayland;",
    ]
    if icon_installed:
        lines.append(f"Icon={ICON_NAME}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_task_watcher_service(binary: Path) -> Path:
    path = task_watcher_service_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = project_root()
    payload = (
        "[Unit]\n"
        "Description=Jugoo task watcher\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={_unit_path(str(root))}\n"
        f"ExecStart={_unit_path(str(binary))} --task-watcher\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "TimeoutStopSec=15\n"
        "KillMode=mixed\n"
        "KillSignal=SIGTERM\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
        "WantedBy=graphical-session.target\n"
    )
    path.write_text(payload, encoding="utf-8")
    _run_optional(["systemctl", "--user", "daemon-reload"])
    return path


def _enable_task_watcher_service() -> None:
    _run_optional(["systemctl", "--user", "enable", _TASK_WATCHER_SERVICE])


def _user_systemd_is_live() -> bool:
    return xdg_config_home().resolve() == (Path.home() / ".config").resolve()


def _install_icon(logo: Path) -> bool:
    suffix = logo.suffix.casefold()
    if suffix == ".svg":
        destination = (
            xdg_data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{ICON_NAME}.svg"
        )
        _copy_file(logo, destination)
        return True
    if suffix == ".png":
        size = _png_size(logo) or 256
        slot = _nearest_hicolor_size(size)
        destination = (
            xdg_data_home()
            / "icons"
            / "hicolor"
            / f"{slot}x{slot}"
            / "apps"
            / f"{ICON_NAME}.png"
        )
        _copy_file(logo, destination)
        return True
    print(f"Jugoo: ignored unsupported logo format: {logo}")
    return False


def _remove_installed_icons() -> None:
    icons = xdg_data_home() / "icons" / "hicolor"
    _remove_file(icons / "scalable" / "apps" / f"{ICON_NAME}.svg")
    for size in _HICOLOR_SIZES:
        _remove_file(icons / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png")


def _refresh_caches() -> None:
    desktop_dir = desktop_file_path().parent
    icons_dir = xdg_data_home() / "icons" / "hicolor"
    _run_optional(["update-desktop-database", str(desktop_dir)])
    if icons_dir.is_dir():
        _run_optional(["gtk-update-icon-cache", "-f", "-t", str(icons_dir)])


def _print_summary(binary: Path, logo: Path | None, icon_installed: bool) -> None:
    print(f"Jugoo: installed launcher {binary}")
    print(f"Jugoo: installed desktop entry {desktop_file_path()}")
    if icon_installed and logo is not None:
        print(f"Jugoo: installed icon from {logo}")
        return
    print(
        "Jugoo: no official logo yet. Place jugoo.svg, jugoo.png, "
        f"{APPLICATION_ID}.svg or logo.svg in {assets_dir()} and rerun "
        "`python3 -m shell --install`."
    )


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _desktop_exec(binary: Path) -> str:
    text = str(binary)
    if any(ch.isspace() for ch in text):
        return f'"{text}"'
    return text


def _unit_path(value: str) -> str:
    if any(ch.isspace() for ch in value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _png_size(path: Path) -> int | None:
    try:
        with path.open("rb") as handle:
            signature = handle.read(8)
            if signature != b"\x89PNG\r\n\x1a\n":
                return None
            length, chunk = struct.unpack(">I4s", handle.read(8))
            if chunk != b"IHDR" or length < 8:
                return None
            width, _height = struct.unpack(">II", handle.read(8))
    except (OSError, struct.error):
        return None
    return int(width)


def _nearest_hicolor_size(width: int) -> int:
    return min(_HICOLOR_SIZES, key=lambda size: (abs(size - width), size))


def _run_optional(command: list[str]) -> None:
    try:
        subprocess.run(command, check=False, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return
