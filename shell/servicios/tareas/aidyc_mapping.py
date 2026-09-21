"""Map AIDYC task fields ↔ Jugoo task status / display models.

Status mapping (explicit):
  AIDYC pendiente  → Jugoo pending   (or overdue if fecha_limite < today)
  AIDYC completada → Jugoo completed

Priority / category:
  AIDYC prioridad (alta|media|baja) → Jugoo priority_id (same string when possible)
  AIDYC categoria → Jugoo category_id
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ...models import (
    TASK_REPEAT_NONE,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_PENDING,
    TaskRecord,
    TaskSnapshot,
)
from .aidyc_cache import AidycTaskLink
from .logic import parse_iso_date


def aidyc_estado_to_jugoo(estado: str, *, fecha_limite: str | None, today: date | None = None) -> str:
    raw = (estado or "").strip().lower()
    if raw == "completada":
        return TASK_STATUS_COMPLETED
    when = today or date.today()
    due = parse_iso_date((fecha_limite or "")[:10] or None)
    if due is not None and due < when:
        return TASK_STATUS_OVERDUE
    return TASK_STATUS_PENDING


def jugoo_status_to_aidyc_action(status: str) -> str | None:
    """Return remote action name for a desired Jugoo-side status change, if any."""
    if status == TASK_STATUS_COMPLETED:
        return "completar"
    if status in (TASK_STATUS_PENDING, TASK_STATUS_OVERDUE):
        return "reabrir"
    return None


def link_to_snapshot(link: AidycTaskLink, *, today: date | None = None) -> TaskSnapshot:
    snap = link.snapshot
    when = today or date.today()
    due = (snap.get("fecha_limite") or "")[:10] or None
    if due == "":
        due = None
    status = aidyc_estado_to_jugoo(
        str(snap.get("estado") or "pendiente"),
        fecha_limite=due,
        today=when,
    )
    created = str(snap.get("fecha_creacion") or link.last_sync or "")
    return TaskSnapshot(
        id=link.local_id,
        title=str(snap.get("titulo") or f"Tarea #{link.aidyc_task_id}"),
        notes=str(snap.get("descripcion") or ""),
        repeat=TASK_REPEAT_NONE,
        due_date=due,
        month_day=1,
        status=status,
        period_key=due or when.isoformat(),
        missed_count=0,
        created_at=created,
        occurrence_date=due or when.isoformat(),
        category_id=_optional(snap.get("categoria")),
        priority_id=_optional(snap.get("prioridad")),
    )


def link_to_record(link: AidycTaskLink) -> TaskRecord:
    snap = link.snapshot
    due = (snap.get("fecha_limite") or "")[:10] or None
    if due == "":
        due = None
    return TaskRecord(
        id=link.local_id,
        title=str(snap.get("titulo") or f"Tarea #{link.aidyc_task_id}"),
        notes=str(snap.get("descripcion") or ""),
        repeat=TASK_REPEAT_NONE,
        due_date=due,
        month_day=1,
        created_at=str(snap.get("fecha_creacion") or ""),
        period_cursor=due or "",
        completed_periods=(),
        missed_periods=(),
        category_id=_optional(snap.get("categoria")),
        priority_id=_optional(snap.get("prioridad")),
    )


def parse_aidyc_local_id(task_id: str) -> int | None:
    text = str(task_id or "").strip()
    if not text.startswith("aidyc:"):
        return None
    try:
        return int(text.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def assignee_label(snapshot: dict[str, Any]) -> str:
    nombre = str(snapshot.get("asignado_nombre") or "").strip()
    usuario = str(snapshot.get("asignado_a") or "").strip()
    if nombre and usuario and nombre != usuario:
        return f"{nombre} ({usuario})"
    return nombre or usuario or "Sin asignar"


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
