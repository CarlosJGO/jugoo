"""Period keys, rollover, and calendar membership for shell tasks."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import replace
from datetime import date, datetime, timedelta

from ...models import (
    TASK_REPEAT_DAILY,
    TASK_REPEAT_MONTHLY,
    TASK_REPEAT_NONE,
    TASK_REPEATS,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_MISSED,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_PENDING,
    TaskRecord,
    TaskSnapshot,
)

MAX_PERIOD_LOG = 120

_MONTH_NAMES = (
    "",
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def created_date(task: TaskRecord) -> date | None:
    raw = (task.created_at or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return parse_iso_date(raw)


def clamp_month_day(year: int, month: int, month_day: int) -> int:
    last = monthrange(year, month)[1]
    return min(max(1, int(month_day)), last)


def period_key(repeat: str, on_date: date, *, due_date: str | None = None) -> str:
    if repeat == TASK_REPEAT_DAILY:
        return on_date.isoformat()
    if repeat == TASK_REPEAT_MONTHLY:
        return f"{on_date.year:04d}-{on_date.month:02d}"
    return (due_date or "once")[:10]


def month_occurrence_date(year: int, month: int, month_day: int) -> date:
    return date(year, month, clamp_month_day(year, month, month_day))


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def iter_elapsed_periods(repeat: str, cursor: str, current: str) -> tuple[str, ...]:
    """Periods after ``cursor`` and before ``current`` (already processed vs now)."""
    if not cursor or cursor == current:
        return ()
    elapsed: list[str] = []
    if repeat == TASK_REPEAT_DAILY:
        start = parse_iso_date(cursor)
        end = parse_iso_date(current)
        if start is None or end is None:
            return ()
        day = start
        while day < end:
            elapsed.append(day.isoformat())
            day += timedelta(days=1)
        return tuple(elapsed)
    if repeat == TASK_REPEAT_MONTHLY:
        try:
            year, month = (int(part) for part in cursor.split("-")[:2])
            end_year, end_month = (int(part) for part in current.split("-")[:2])
        except ValueError:
            return ()
        while (year, month) < (end_year, end_month):
            elapsed.append(f"{year:04d}-{month:02d}")
            year, month = add_months(year, month, 1)
        return tuple(elapsed)
    return ()


def rollover_task(task: TaskRecord, today: date) -> TaskRecord:
    if task.repeat not in (TASK_REPEAT_DAILY, TASK_REPEAT_MONTHLY):
        return task
    current = period_key(task.repeat, today, due_date=task.due_date)
    if not task.period_cursor:
        return replace(task, period_cursor=current)
    if task.period_cursor == current:
        return task
    missed = list(task.missed_periods)
    completed = set(task.completed_periods)
    for key in iter_elapsed_periods(task.repeat, task.period_cursor, current):
        if key not in completed and key not in missed:
            missed.append(key)
    return replace(
        task,
        period_cursor=current,
        missed_periods=_trim_period_log(tuple(missed)),
        completed_periods=_trim_period_log(task.completed_periods),
    )


def rollover_tasks(tasks: tuple[TaskRecord, ...], today: date) -> tuple[TaskRecord, ...]:
    return tuple(rollover_task(task, today) for task in tasks)


def toggle_task(task: TaskRecord, today: date) -> TaskRecord:
    key = period_key(task.repeat, today, due_date=task.due_date)
    completed = set(task.completed_periods)
    if key in completed:
        completed.remove(key)
    else:
        completed.add(key)
    return replace(task, completed_periods=tuple(sorted(completed)))


def applies_on(task: TaskRecord, on_date: date) -> bool:
    born = created_date(task)
    if born is not None and on_date < born:
        return False
    if task.repeat == TASK_REPEAT_DAILY:
        return True
    if task.repeat == TASK_REPEAT_MONTHLY:
        return on_date.day == clamp_month_day(on_date.year, on_date.month, task.month_day)
    due = parse_iso_date(task.due_date)
    return due == on_date


def is_overdue_one_shot(task: TaskRecord, today: date) -> bool:
    if task.repeat != TASK_REPEAT_NONE:
        return False
    due = parse_iso_date(task.due_date)
    if due is None or due >= today:
        return False
    key = period_key(task.repeat, today, due_date=task.due_date)
    return key not in task.completed_periods


def task_status(task: TaskRecord, on_date: date, today: date) -> str:
    key = period_key(task.repeat, on_date, due_date=task.due_date)
    if key in task.completed_periods:
        return TASK_STATUS_COMPLETED
    if task.repeat == TASK_REPEAT_NONE:
        due = parse_iso_date(task.due_date)
        if due is not None and due < today:
            return TASK_STATUS_OVERDUE
        return TASK_STATUS_PENDING
    if on_date < today:
        if key in task.missed_periods or key < period_key(task.repeat, today, due_date=task.due_date):
            return TASK_STATUS_MISSED
    return TASK_STATUS_PENDING


def snapshot_for(task: TaskRecord, on_date: date, today: date) -> TaskSnapshot:
    return TaskSnapshot(
        id=task.id,
        title=task.title,
        notes=task.notes,
        repeat=task.repeat if task.repeat in TASK_REPEATS else TASK_REPEAT_NONE,
        due_date=task.due_date,
        month_day=clamp_month_day(on_date.year, on_date.month, task.month_day),
        status=task_status(task, on_date, today),
        period_key=period_key(task.repeat, on_date, due_date=task.due_date),
        missed_count=len(task.missed_periods),
        created_at=task.created_at,
        occurrence_date=on_date.isoformat(),
    )


def tasks_for_date(
    tasks: tuple[TaskRecord, ...],
    on_date: date,
    today: date,
) -> tuple[TaskSnapshot, ...]:
    visible = [snapshot_for(task, on_date, today) for task in tasks if applies_on(task, on_date)]
    return tuple(sorted(visible, key=_snapshot_sort_key))


def today_board(
    tasks: tuple[TaskRecord, ...],
    today: date,
) -> tuple[TaskSnapshot, ...]:
    board: list[TaskSnapshot] = []
    seen: set[str] = set()
    for task in tasks:
        if applies_on(task, today) or is_overdue_one_shot(task, today):
            snapshot = snapshot_for(task, today, today)
            board.append(snapshot)
            seen.add(task.id)
    return tuple(sorted(board, key=_snapshot_sort_key))


def upcoming_tasks(
    tasks: tuple[TaskRecord, ...],
    today: date,
    *,
    horizon_days: int = 62,
) -> tuple[TaskSnapshot, ...]:
    upcoming: list[TaskSnapshot] = []
    last = today + timedelta(days=horizon_days)
    day = today + timedelta(days=1)
    seen: set[tuple[str, str]] = set()
    while day <= last:
        for snapshot in tasks_for_date(tasks, day, today):
            if snapshot.repeat == TASK_REPEAT_DAILY:
                continue
            stamp = (snapshot.id, snapshot.period_key)
            if stamp in seen:
                continue
            seen.add(stamp)
            upcoming.append(snapshot)
        day += timedelta(days=1)
    return tuple(sorted(upcoming, key=_snapshot_sort_key))


def marked_days(
    tasks: tuple[TaskRecord, ...],
    year: int,
    month: int,
) -> frozenset[int]:
    days: set[int] = set()
    last = monthrange(year, month)[1]
    for task in tasks:
        if task.repeat == TASK_REPEAT_DAILY:
            continue
        if task.repeat == TASK_REPEAT_MONTHLY:
            days.add(clamp_month_day(year, month, task.month_day))
            continue
        due = parse_iso_date(task.due_date)
        if due is not None and due.year == year and due.month == month:
            days.add(due.day)
    return frozenset(day for day in days if 1 <= day <= last)


def pending_today_count(tasks: tuple[TaskRecord, ...], today: date) -> int:
    return sum(
        1
        for snapshot in today_board(tasks, today)
        if snapshot.status in (TASK_STATUS_PENDING, TASK_STATUS_OVERDUE)
    )


def overdue_count(tasks: tuple[TaskRecord, ...], today: date) -> int:
    return sum(1 for task in tasks if is_overdue_one_shot(task, today))


def repeat_label(repeat: str) -> str:
    if repeat == TASK_REPEAT_DAILY:
        return "Cada día"
    if repeat == TASK_REPEAT_MONTHLY:
        return "Cada mes"
    return "Una vez"


def format_day_label(value: date) -> str:
    return f"{value.day} {_MONTH_NAMES[value.month]}"


def meta_label(snapshot: TaskSnapshot) -> str:
    if snapshot.status == TASK_STATUS_OVERDUE:
        due = parse_iso_date(snapshot.due_date)
        if due is not None:
            return f"Vencida · {format_day_label(due)}"
        return "Vencida"
    if snapshot.status == TASK_STATUS_MISSED:
        return "No completada"
    if snapshot.repeat == TASK_REPEAT_DAILY:
        return "Cada día"
    if snapshot.repeat == TASK_REPEAT_MONTHLY:
        return f"Cada mes · día {snapshot.month_day}"
    due = parse_iso_date(snapshot.due_date)
    if due is not None:
        return f"Vence {format_day_label(due)}"
    return "Sin fecha"


def _snapshot_sort_key(snapshot: TaskSnapshot) -> tuple[int, str, str]:
    status_rank = {
        TASK_STATUS_OVERDUE: 0,
        TASK_STATUS_PENDING: 1,
        TASK_STATUS_MISSED: 2,
        TASK_STATUS_COMPLETED: 3,
    }.get(snapshot.status, 4)
    return (status_rank, snapshot.occurrence_date, snapshot.title.casefold())


def _trim_period_log(periods: tuple[str, ...]) -> tuple[str, ...]:
    if len(periods) <= MAX_PERIOD_LOG:
        return periods
    return periods[-MAX_PERIOD_LOG:]
