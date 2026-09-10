"""Minimal persistence for the last startup briefing message (no history)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...runtime_paths import briefing_path


@dataclass(frozen=True)
class BriefingMemory:
    """Only the latest assistant message plus a compact state fingerprint."""

    last_message: str
    generated_at: str
    open_ids: tuple[str, ...] = ()
    overdue: int = 0
    pending_today: int = 0
    upcoming: int = 0


def load_briefing_memory(path: Path | None = None) -> BriefingMemory | None:
    """Return the last briefing, or ``None`` if missing/corrupt/empty."""
    target = path if path is not None else briefing_path()
    try:
        if not target.is_file():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    message = str(payload.get("last_message", "")).strip()
    if not message:
        return None
    generated_at = str(payload.get("generated_at", "")).strip()
    state = payload.get("state")
    open_ids: tuple[str, ...] = ()
    overdue = pending_today = upcoming = 0
    if isinstance(state, dict):
        raw_ids = state.get("open_ids", ())
        if isinstance(raw_ids, list):
            open_ids = tuple(str(item) for item in raw_ids if str(item).strip())
        overdue = _as_nonneg_int(state.get("overdue"))
        pending_today = _as_nonneg_int(state.get("pending_today"))
        upcoming = _as_nonneg_int(state.get("upcoming"))
    return BriefingMemory(
        last_message=message,
        generated_at=generated_at,
        open_ids=open_ids,
        overdue=overdue,
        pending_today=pending_today,
        upcoming=upcoming,
    )


def save_briefing_memory(
    message: str,
    *,
    open_ids: tuple[str, ...] = (),
    overdue: int = 0,
    pending_today: int = 0,
    upcoming: int = 0,
    path: Path | None = None,
) -> None:
    """Replace any previous briefing with the newest message only."""
    cleaned = message.strip()
    if not cleaned:
        return
    target = path if path is not None else briefing_path()
    payload = {
        "last_message": cleaned,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "state": {
            "open_ids": list(open_ids),
            "overdue": int(overdue),
            "pending_today": int(pending_today),
            "upcoming": int(upcoming),
        },
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # Persistence must never break the briefing notification path.
        return


def describe_briefing_changes(
    previous: BriefingMemory | None,
    *,
    open_ids: tuple[str, ...],
    overdue: int,
    pending_today: int,
    upcoming: int,
) -> str:
    """Human-readable change summary prepared by Python (not by Llama)."""
    lines = ["CAMBIOS DESDE EL ÚLTIMO BRIEFING:"]
    if previous is None:
        lines.append("- Primer briefing (sin memoria previa).")
        return "\n".join(lines)

    previous_ids = set(previous.open_ids)
    current_ids = set(open_ids)
    added = current_ids - previous_ids
    removed = previous_ids - current_ids
    relevant = False

    if removed:
        count = len(removed)
        label = "tarea" if count == 1 else "tareas"
        lines.append(f"- Salieron {count} {label} de la lista abierta.")
        relevant = True
    if added:
        count = len(added)
        label = "tarea nueva" if count == 1 else "tareas nuevas"
        lines.append(f"- Se agregó {count} {label}.")
        relevant = True

    still_open = previous_ids & current_ids
    if still_open:
        count = len(still_open)
        label = (
            "tarea sigue en la lista abierta"
            if count == 1
            else "tareas siguen en la lista abierta"
        )
        lines.append(f"- {count} {label}.")
        relevant = True

    if previous.overdue != overdue:
        lines.append(f"- Vencidas: {previous.overdue} → {overdue}.")
        relevant = True
    if previous.pending_today != pending_today:
        lines.append(f"- Para hoy: {previous.pending_today} → {pending_today}.")
        relevant = True
    if previous.upcoming != upcoming:
        lines.append(f"- Próximas: {previous.upcoming} → {upcoming}.")
        relevant = True

    if not relevant:
        lines.append("- No hubo cambios relevantes.")
    return "\n".join(lines)


def _as_nonneg_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)
