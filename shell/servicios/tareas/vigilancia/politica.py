"""Deterministic reminder policy. Never uses AI to decide whether to notify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Sequence

from ....models import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_PENDING,
    TaskSnapshot,
)
from ..logic import parse_iso_date
from .config import WatcherConfig
from .estado import ReminderState


@dataclass(frozen=True)
class ActivitySnapshot:
    distracted: bool = False
    distracted_for_sec: float = 0.0
    app_class: str = ""
    title: str = ""
    label: str = "desktop"


@dataclass(frozen=True)
class ReminderDecision:
    should_notify: bool
    reason: str
    snapshot: TaskSnapshot | None = None
    seconds_until_due: float | None = None
    distracted_for_sec: float = 0.0


def due_datetime(snapshot: TaskSnapshot) -> datetime:
    due = parse_iso_date(snapshot.due_date)
    if due is None:
        due = parse_iso_date(snapshot.occurrence_date)
    if due is None:
        due = date.today()
    return datetime.combine(due, time(23, 59, 59))


def due_label(snapshot: TaskSnapshot, now: datetime) -> str:
    due = due_datetime(snapshot)
    if snapshot.status == TASK_STATUS_OVERDUE or due < now:
        days = (now.date() - due.date()).days
        if days <= 0:
            return "vencida"
        if days == 1:
            return "vencida desde ayer"
        return f"vencida hace {days} días"
    hours = (due - now).total_seconds() / 3600.0
    if hours <= 1:
        return "vence muy pronto"
    if hours <= 3:
        return "vence en un par de horas"
    return f"today {due.strftime('%H:%M')}"


def cooldown_seconds(reminder_count: int, base: int) -> float:
    exponent = min(max(reminder_count - 1, 0), 3)
    return float(base) * (2 ** exponent)


def choose_reminder(
    snapshots: Sequence[TaskSnapshot],
    state: ReminderState,
    activity: ActivitySnapshot,
    *,
    now: datetime,
    config: WatcherConfig,
) -> ReminderDecision:
    """Return at most one reminder. Priority: overdue, then soonest due."""
    empty = ReminderDecision(False, "none", distracted_for_sec=activity.distracted_for_sec)
    if not config.enabled:
        return ReminderDecision(False, "disabled", distracted_for_sec=activity.distracted_for_sec)

    ranked: list[tuple[int, float, TaskSnapshot, float]] = []
    skipped: list[ReminderDecision] = []
    for snapshot in snapshots:
        if snapshot.status == TASK_STATUS_COMPLETED:
            state.forget(snapshot.id)
            continue
        if snapshot.status not in (TASK_STATUS_PENDING, TASK_STATUS_OVERDUE):
            continue
        decision = _evaluate_task(snapshot, state, activity, now=now, config=config)
        if not decision.should_notify or decision.snapshot is None:
            skipped.append(decision)
            continue
        remaining = decision.seconds_until_due if decision.seconds_until_due is not None else 0.0
        overdue_rank = 0 if snapshot.status == TASK_STATUS_OVERDUE or remaining < 0 else 1
        ranked.append((overdue_rank, remaining, snapshot, activity.distracted_for_sec))

    if not ranked:
        return skipped[0] if skipped else empty
    ranked.sort(key=lambda item: (item[0], item[1], item[2].title.casefold()))
    _, remaining, snapshot, distracted = ranked[0]
    reason = "overdue" if remaining < 0 or snapshot.status == TASK_STATUS_OVERDUE else (
        "urgent" if remaining <= config.urgent_window_sec else "distracted"
    )
    return ReminderDecision(
        True,
        reason,
        snapshot,
        remaining,
        distracted,
    )


def _evaluate_task(
    snapshot: TaskSnapshot,
    state: ReminderState,
    activity: ActivitySnapshot,
    *,
    now: datetime,
    config: WatcherConfig,
) -> ReminderDecision:
    now_ts = now.timestamp()
    memory = state.align_period(snapshot.id, snapshot.period_key)
    remaining = (due_datetime(snapshot) - now).total_seconds()
    distracted_long_enough = (
        activity.distracted and activity.distracted_for_sec >= config.distraction_threshold_sec
    )

    if memory.snooze_until > now_ts:
        return ReminderDecision(False, "snooze", snapshot, remaining, activity.distracted_for_sec)
    if memory.reminder_count >= config.max_reminders_per_occurrence:
        return ReminderDecision(False, "exhausted", snapshot, remaining, activity.distracted_for_sec)
    if memory.last_notified_at:
        wait = cooldown_seconds(memory.reminder_count, config.reminder_cooldown_sec)
        if now_ts - memory.last_notified_at < wait:
            return ReminderDecision(False, "cooldown", snapshot, remaining, activity.distracted_for_sec)

    overdue = snapshot.status == TASK_STATUS_OVERDUE or remaining < 0
    urgent = 0 <= remaining <= config.urgent_window_sec
    relevant_soon = 0 <= remaining <= config.future_horizon_sec
    if overdue or urgent or (distracted_long_enough and relevant_soon):
        reason = "overdue" if overdue else ("urgent" if urgent else "distracted")
        return ReminderDecision(True, reason, snapshot, remaining, activity.distracted_for_sec)
    if remaining > config.future_horizon_sec:
        return ReminderDecision(False, "future", snapshot, remaining, activity.distracted_for_sec)
    return ReminderDecision(False, "quiet", snapshot, remaining, activity.distracted_for_sec)
