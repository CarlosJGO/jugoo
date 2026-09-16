"""Tests for stable assets/usuario and assets/pc profile image copies."""

from __future__ import annotations

from pathlib import Path

import shell.ui.profile_images as profile_images
from shell.ui.profile_images import (
    AVATAR_SETTING_KEY,
    MACHINE_SETTING_KEY,
    install_profile_image,
    resolve_profile_image,
)


def test_install_profile_image_copies_and_replaces(tmp_path: Path, monkeypatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(profile_images, "assets_dir", lambda: assets)

    source = tmp_path / "face.jpg"
    source.write_bytes(b"jpeg-bytes-1")
    installed = install_profile_image(source, AVATAR_SETTING_KEY)
    assert installed == assets / "usuario.jpg"
    assert installed.read_bytes() == b"jpeg-bytes-1"
    assert resolve_profile_image(AVATAR_SETTING_KEY) == installed

    replacement = tmp_path / "face.png"
    replacement.write_bytes(b"png-bytes-2")
    installed2 = install_profile_image(replacement, AVATAR_SETTING_KEY)
    assert installed2 == assets / "usuario.png"
    assert installed2.read_bytes() == b"png-bytes-2"
    assert not (assets / "usuario.jpg").exists()
    assert list(assets.glob("usuario.*")) == [installed2]


def test_install_machine_image_uses_pc_stem(tmp_path: Path, monkeypatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(profile_images, "assets_dir", lambda: assets)

    source = tmp_path / "tower.webp"
    source.write_bytes(b"webp")
    installed = install_profile_image(source, MACHINE_SETTING_KEY)
    assert installed.name == "pc.webp"
    assert resolve_profile_image(MACHINE_SETTING_KEY) == installed


if __name__ == "__main__":
    import tempfile

    class _FakeMonkey:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    with tempfile.TemporaryDirectory() as tmp:
        # Minimal runner without pytest monkeypatch.
        assets = Path(tmp) / "assets"
        assets.mkdir()
        original = profile_images.assets_dir
        profile_images.assets_dir = lambda: assets  # type: ignore[assignment]
        try:
            source = Path(tmp) / "face.jpg"
            source.write_bytes(b"jpeg-bytes-1")
            installed = install_profile_image(source, AVATAR_SETTING_KEY)
            assert installed.name == "usuario.jpg"
            replacement = Path(tmp) / "face.png"
            replacement.write_bytes(b"png-bytes-2")
            installed2 = install_profile_image(replacement, AVATAR_SETTING_KEY)
            assert installed2.name == "usuario.png"
            assert not (assets / "usuario.jpg").exists()
            machine = Path(tmp) / "tower.webp"
            machine.write_bytes(b"webp")
            pc = install_profile_image(machine, MACHINE_SETTING_KEY)
            assert pc.name == "pc.webp"
        finally:
            profile_images.assets_dir = original  # type: ignore[assignment]
    print("profile images OK")
