"""User-defined profile fields for the control-center user pane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_PROFILE_FIELDS = 24


@dataclass(frozen=True)
class ProfileField:
    title: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "value": self.value}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProfileField | None:
        title = str(payload.get("title", "")).strip()
        value = str(payload.get("value", "")).strip()
        if not title and not value:
            return None
        return cls(title=title or "Dato", value=value)


def parse_profile_fields(raw: str | None) -> tuple[ProfileField, ...]:
    if not raw or not str(raw).strip():
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if isinstance(payload, dict):
        # Legacy / alternate shape: {title: value, ...}
        fields: list[ProfileField] = []
        for title, value in payload.items():
            field = ProfileField.from_dict({"title": title, "value": value})
            if field is not None:
                fields.append(field)
            if len(fields) >= MAX_PROFILE_FIELDS:
                break
        return tuple(fields)
    if not isinstance(payload, list):
        return ()
    fields = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        field = ProfileField.from_dict(item)
        if field is not None:
            fields.append(field)
        if len(fields) >= MAX_PROFILE_FIELDS:
            break
    return tuple(fields)


def serialize_profile_fields(fields: tuple[ProfileField, ...] | list[ProfileField]) -> str:
    limited = list(fields)[:MAX_PROFILE_FIELDS]
    return json.dumps([field.to_dict() for field in limited], ensure_ascii=False)
