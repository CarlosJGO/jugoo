"""Copy SDDM backgrounds into the theme staging tree with safe filenames."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def install_background(source: Path, backgrounds_dir: Path) -> str:
    """Copy ``source`` into ``backgrounds_dir`` and return a theme-relative path.

    The destination name is ASCII-safe (hash + original suffix) so SDDM never
    depends on spaces or Unicode in the filename. The source path may contain
    both.
    """
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))

    suffix = source.suffix.casefold()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported background type: {suffix or '(none)'}")

    backgrounds_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    dest_name = f"user-{digest}{suffix}"
    dest = backgrounds_dir / dest_name
    shutil.copy2(source, dest)
    return f"Backgrounds/{dest_name}"


def clear_user_backgrounds(backgrounds_dir: Path) -> None:
    """Remove previously staged user-* backgrounds (keep other assets)."""
    if not backgrounds_dir.is_dir():
        return
    for path in backgrounds_dir.glob("user-*"):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
