"""Persistent conversation memory for the under-bar AI ask prompt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from ...runtime_paths import ai_chat_path

Role = Literal["user", "assistant"]

# Keep enough turns for continuity without blowing cold llama-cli context.
_MAX_MESSAGES = 12
_MAX_CONTENT_CHARS = 1200


@dataclass(frozen=True)
class ChatTurn:
    role: Role
    content: str
    at: str = ""


def load_chat_memory(path: Path | None = None) -> tuple[ChatTurn, ...]:
    target = path if path is not None else ai_chat_path()
    try:
        if not target.is_file():
            return ()
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("messages")
    if not isinstance(raw, list):
        return ()
    turns: list[ChatTurn] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().casefold()
        content = " ".join(str(item.get("content", "")).split()).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        turns.append(
            ChatTurn(
                role=role,  # type: ignore[arg-type]
                content=content[:_MAX_CONTENT_CHARS],
                at=str(item.get("at", "")).strip(),
            )
        )
    return tuple(turns[-_MAX_MESSAGES:])


def save_chat_memory(turns: Iterable[ChatTurn], *, path: Path | None = None) -> None:
    target = path if path is not None else ai_chat_path()
    cleaned = tuple(turns)[-_MAX_MESSAGES:]
    payload = {
        "messages": [
            {
                "role": turn.role,
                "content": turn.content[:_MAX_CONTENT_CHARS],
                "at": turn.at
                or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
            for turn in cleaned
        ]
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def clear_chat_memory(*, path: Path | None = None) -> None:
    target = path if path is not None else ai_chat_path()
    try:
        if target.is_file():
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps({"messages": []}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except OSError:
        return


def append_chat_turn(
    role: Role,
    content: str,
    *,
    path: Path | None = None,
) -> tuple[ChatTurn, ...]:
    text = " ".join(str(content or "").split()).strip()
    if not text:
        return load_chat_memory(path)
    turns = list(load_chat_memory(path))
    turns.append(
        ChatTurn(
            role=role,
            content=text[:_MAX_CONTENT_CHARS],
            at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
    )
    saved = tuple(turns[-_MAX_MESSAGES:])
    save_chat_memory(saved, path=path)
    return saved


def build_prompt_with_memory(user_text: str, history: Iterable[ChatTurn]) -> str:
    """Embed prior turns as a short chat transcript (matches manual llama use)."""
    current = " ".join(str(user_text or "").split()).strip()
    prior = tuple(history)
    if not prior:
        return current
    lines = ["Conversación reciente:"]
    for turn in prior:
        label = "Usuario" if turn.role == "user" else "Jugoo"
        lines.append(f"{label}: {turn.content}")
    lines.append(f"Usuario: {current}")
    return "\n".join(lines)
