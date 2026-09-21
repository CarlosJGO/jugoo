"""Desktop wallpaper install/copy and backend apply (mocked processes)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from shell.settings.schema import settings_by_key
from shell.settings.wallpaper import (
    WallpaperService,
    current_installed_wallpaper,
    install_wallpaper,
)


class FakeProcess:
    def __init__(self, command, **_kwargs) -> None:
        self.command = list(command)
        self.pid = 4242
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.terminated = True


def _hyprctl_recorder(bucket: list[list[str]]):
    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        bucket.append(list(args))
        return subprocess.CompletedProcess(["hyprctl", *args], 0, "", "")

    return runner


def test_install_wallpaper_copies_and_replaces(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dest = tmp_path / "wallpapers"
    source = tmp_path / "nebula.jpg"
    source.write_bytes(b"jpeg-1")
    installed = install_wallpaper(source, dest)
    assert installed == dest / "current.jpg"
    assert installed.read_bytes() == b"jpeg-1"
    assert current_installed_wallpaper(dest) == installed

    replacement = tmp_path / "orion.png"
    replacement.write_bytes(b"png-2")
    installed2 = install_wallpaper(replacement, dest)
    assert installed2 == dest / "current.png"
    assert installed2.read_bytes() == b"png-2"
    assert not (dest / "current.jpg").exists()


def test_install_wallpaper_rejects_unsupported_type(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "notes.txt"
    source.write_text("nope", encoding="utf-8")
    try:
        install_wallpaper(source, tmp_path / "wallpapers")
    except ValueError as error:
        assert "unsupported wallpaper type" in str(error)
        return
    raise AssertionError("expected ValueError")


def test_apply_starts_swaybg_with_fill_mode(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    image = tmp_path / "bg.png"
    image.write_bytes(b"\x89PNG")
    started: list[FakeProcess] = []
    hypr: list[list[str]] = []

    def popen(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        started.append(process)
        return process

    service = WallpaperService(
        dest_dir=tmp_path / "wallpapers",
        pid_file=tmp_path / "wallpaper.pid",
        which=lambda name: "/usr/bin/swaybg" if name == "swaybg" else None,
        popen=popen,
        hyprctl=_hyprctl_recorder(hypr),
    )
    status = service.configure(path=str(image), fill="fit")
    assert status.active
    assert status.backend == "swaybg"
    assert "fit" in status.message
    assert started[0].command[:2] == ["swaybg", "-i"]
    assert started[0].command[-2:] == ["-m", "fit"]
    assert ["keyword", "misc:disable_hyprland_logo", "true"] in hypr
    assert ["keyword", "misc:force_default_wallpaper", "0"] in hypr
    assert (tmp_path / "wallpapers" / "current.png").is_file()


def test_apply_empty_path_stops_and_clears_copy(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    hypr: list[list[str]] = []
    dest = tmp_path / "wallpapers"
    dest.mkdir()
    leftover = dest / "current.jpg"
    leftover.write_bytes(b"old")

    service = WallpaperService(
        dest_dir=dest,
        pid_file=tmp_path / "wallpaper.pid",
        which=lambda name: "/usr/bin/swaybg" if name == "swaybg" else None,
        popen=FakeProcess,
        hyprctl=_hyprctl_recorder(hypr),
    )
    status = service.configure(path="", fill="crop")
    assert not status.active
    assert "Sin fondo" in status.message
    assert not leftover.exists()
    assert hypr == []


def test_apply_without_backend_keeps_copy(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    image = tmp_path / "bg.webp"
    image.write_bytes(b"RIFF")
    service = WallpaperService(
        dest_dir=tmp_path / "wallpapers",
        pid_file=tmp_path / "wallpaper.pid",
        which=lambda _name: None,
        popen=FakeProcess,
        hyprctl=_hyprctl_recorder([]),
    )
    status = service.configure(path=str(image), fill="crop")
    assert not status.active
    assert "swaybg" in status.message
    assert (tmp_path / "wallpapers" / "current.webp").is_file()


def test_catalog_includes_desktop_wallpaper_keys() -> None:
    keys = settings_by_key()
    assert keys["escritorio.wallpaper_path"].category.value == "tema"
    assert keys["escritorio.wallpaper_fill"].choices[0][0] == "crop"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        test_install_wallpaper_copies_and_replaces(root / "copy")
        test_install_wallpaper_rejects_unsupported_type(root / "bad")
        test_apply_starts_swaybg_with_fill_mode(root / "swaybg")
        test_apply_empty_path_stops_and_clears_copy(root / "clear")
        test_apply_without_backend_keeps_copy(root / "missing")
    test_catalog_includes_desktop_wallpaper_keys()
    print("wallpaper OK")
