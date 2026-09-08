"""JSON persistence for user settings under XDG data."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .schema import SettingDef, build_settings_catalog, settings_by_key

SETTINGS_VERSION = 1


def default_settings_path() -> Path:
    try:
        from ..runtime_paths import settings_path

        return settings_path()
    except Exception:
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return data / "waybar-shell" / "settings.json"


class SettingsStore:
    """Load/save overrides. Missing keys fall back to schema defaults."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        catalog: tuple[SettingDef, ...] | None = None,
    ) -> None:
        self._path = path if path is not None else default_settings_path()
        self._catalog = catalog if catalog is not None else build_settings_catalog()
        self._defs = settings_by_key(self._catalog)
        self._values: dict[str, Any] = {
            item.key: _clone(item.default) for item in self._catalog
        }
        self._dirty = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def catalog(self) -> tuple[SettingDef, ...]:
        return self._catalog

    def load(self) -> None:
        if not self._path.is_file():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            print(f"Jugoo settings: could not read {self._path}: {error}")
            return
        if not isinstance(payload, dict):
            return
        values = payload.get("values")
        if not isinstance(values, dict):
            # Accept flat legacy-style {key: value} files too.
            values = payload if "version" not in payload else {}
        for key, value in values.items():
            if key not in self._defs:
                continue
            coerced = _coerce(self._defs[key], value)
            if coerced is not None:
                self._values[key] = coerced

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        overrides = {
            key: value
            for key, value in self._values.items()
            if key in self._defs and value != self._defs[key].default
        }
        payload = {
            "version": SETTINGS_VERSION,
            "values": overrides,
        }
        encoded = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        fd, temporary_name = tempfile.mkstemp(
            prefix=".settings-",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
            self._dirty = False
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def get(self, key: str) -> Any:
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]

    def set(self, key: str, value: Any) -> bool:
        definition = self._defs.get(key)
        if definition is None:
            raise KeyError(key)
        coerced = _coerce(definition, value)
        if coerced is None:
            return False
        if self._values.get(key) == coerced:
            return False
        self._values[key] = coerced
        self._dirty = True
        return True

    def reset(self, key: str) -> bool:
        definition = self._defs.get(key)
        if definition is None:
            raise KeyError(key)
        return self.set(key, definition.default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def definition(self, key: str) -> SettingDef:
        return self._defs[key]


def _clone(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value))
    return value


def _coerce(definition: SettingDef, value: Any) -> Any | None:
    try:
        if definition.value_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
            return None
        if definition.value_type == "int":
            number = int(value)
            if definition.minimum is not None:
                number = max(int(definition.minimum), number)
            if definition.maximum is not None:
                number = min(int(definition.maximum), number)
            return number
        if definition.value_type == "float":
            number = float(value)
            if definition.minimum is not None:
                number = max(float(definition.minimum), number)
            if definition.maximum is not None:
                number = min(float(definition.maximum), number)
            return number
        if definition.value_type in {"string", "path", "choice"}:
            text = str(value)
            if definition.value_type == "choice" and definition.choices:
                allowed = {item for item, _label in definition.choices}
                if allowed and text not in allowed:
                    return None
            return text
    except (TypeError, ValueError):
        return None
    return None
