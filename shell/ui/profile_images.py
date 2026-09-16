"""Persist user/PC profile images under Jugoo assets with stable filenames."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..identity import assets_dir

AVATAR_SETTING_KEY = "general.avatar_path"
MACHINE_SETTING_KEY = "general.machine_image_path"

_PROFILE_STEMS = {
    AVATAR_SETTING_KEY: "usuario",
    MACHINE_SETTING_KEY: "pc",
}

_ALLOWED_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
)


def profile_image_stem(setting_key: str) -> str | None:
    return _PROFILE_STEMS.get(setting_key)


def is_profile_image_setting(setting_key: str) -> bool:
    return setting_key in _PROFILE_STEMS


def resolve_profile_image(setting_key: str) -> Path | None:
    """Return the installed asset for ``setting_key``, if present."""
    stem = profile_image_stem(setting_key)
    if stem is None:
        return None
    root = assets_dir()
    if not root.is_dir():
        return None
    matches = sorted(
        path
        for path in root.glob(f"{stem}.*")
        if path.is_file() and path.suffix.casefold() in _ALLOWED_SUFFIXES
    )
    return matches[0] if matches else None


def install_profile_image(source: Path, setting_key: str) -> Path:
    """Copy ``source`` into assets as ``usuario.*`` / ``pc.*``, replacing any previous file."""
    stem = profile_image_stem(setting_key)
    if stem is None:
        raise ValueError(f"unknown profile image setting: {setting_key}")
    source = Path(source).expanduser()
    if not source.is_file():
        raise FileNotFoundError(str(source))

    root = assets_dir()
    root.mkdir(parents=True, exist_ok=True)

    suffix = source.suffix.casefold()
    if suffix not in _ALLOWED_SUFFIXES:
        suffix = ".png"

    # Drop previous copies (any extension) so we never accumulate variants.
    for old in root.glob(f"{stem}.*"):
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass

    dest = root / f"{stem}{suffix}"
    shutil.copy2(source, dest)
    return dest
