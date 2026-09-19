"""Read the effective SDDM Theme.Current from the real config merge order."""

from __future__ import annotations

import configparser
from pathlib import Path

# sddm.conf(5): directories first (alphabetical), then /etc/sddm.conf (highest precedence).
_SYSTEM_DROPIN_DIR = Path("/usr/lib/sddm/sddm.conf.d")
_LOCAL_DROPIN_DIR = Path("/etc/sddm.conf.d")
_LOCAL_CONF = Path("/etc/sddm.conf")


def config_search_paths() -> tuple[Path, ...]:
    """Return conf files in SDDM load order (later overrides earlier)."""
    paths: list[Path] = []
    for directory in (_SYSTEM_DROPIN_DIR, _LOCAL_DROPIN_DIR):
        if directory.is_dir():
            paths.extend(sorted(p for p in directory.glob("*.conf") if p.is_file()))
    if _LOCAL_CONF.is_file():
        paths.append(_LOCAL_CONF)
    return tuple(paths)


def read_effective_current(
    *,
    paths: tuple[Path, ...] | None = None,
) -> str:
    """Return the effective ``[Theme] Current`` value (may be empty)."""
    current = ""
    for path in paths if paths is not None else config_search_paths():
        value = _read_theme_current(path)
        if value is not None:
            current = value
    return current.strip()


def _read_theme_current(path: Path) -> str | None:
    """Return Current if the file sets it; ``None`` if the key is absent."""
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment, method-assign]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    if not parser.has_section("Theme"):
        return None
    if not parser.has_option("Theme", "Current"):
        return None
    return parser.get("Theme", "Current")
