from __future__ import annotations

from datetime import date
from pathlib import Path
import os

from shell.eventbus import EventBus
from shell.models import (
    TASK_REPEAT_DAILY,
    TASK_REPEAT_MONTHLY,
    TASK_REPEAT_NONE,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_MISSED,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_PENDING,
    TaskRecord,
)
from shell.servicios.tareas.logic import (
    applies_on,
    complete_task,
    marked_days,
    pending_today_count,
    period_key,
    rollover_task,
    snapshot_for,
    tasks_for_date,
    today_board,
    toggle_task,
)
from shell.servicios.tareas.store import load_tasks, save_tasks
from shell.servicios.tareas.tasks import TASKS_CHANGED, TasksService


def _daily(today: date, *, completed: tuple[str, ...] = (), cursor: str | None = None) -> TaskRecord:
    return TaskRecord(
        id="water",
        title="Agua",
        repeat=TASK_REPEAT_DAILY,
        created_at=today.isoformat() + "T08:00:00",
        period_cursor=cursor if cursor is not None else today.isoformat(),
        completed_periods=completed,
    )


def test_daily_period_key_is_the_calendar_day() -> None:
    assert period_key(TASK_REPEAT_DAILY, date(2026, 9, 5)) == "2026-09-05"


def test_monthly_period_key_is_year_month() -> None:
    assert period_key(TASK_REPEAT_MONTHLY, date(2026, 9, 5)) == "2026-09"


def test_daily_rollover_misses_yesterday_and_resets_today() -> None:
    yesterday = date(2026, 9, 4)
    today = date(2026, 9, 5)
    task = _daily(yesterday)
    rolled = rollover_task(task, today)
    assert "2026-09-04" in rolled.missed_periods
    assert rolled.period_cursor == "2026-09-05"
    snap = snapshot_for(rolled, today, today)
    assert snap.status == TASK_STATUS_PENDING


def test_daily_rollover_catches_up_after_several_days() -> None:
    task = _daily(date(2026, 9, 1), cursor="2026-09-01")
    rolled = rollover_task(task, date(2026, 9, 5))
    assert rolled.missed_periods == ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")
    assert rolled.period_cursor == "2026-09-05"


def test_completed_daily_does_not_count_as_missed() -> None:
    yesterday = date(2026, 9, 4)
    today = date(2026, 9, 5)
    task = _daily(yesterday, completed=("2026-09-04",), cursor="2026-09-04")
    rolled = rollover_task(task, today)
    assert "2026-09-04" not in rolled.missed_periods
    assert snapshot_for(rolled, today, today).status == TASK_STATUS_PENDING
    assert snapshot_for(rolled, yesterday, today).status == TASK_STATUS_COMPLETED


def test_daily_toggle_completes_only_current_period() -> None:
    today = date(2026, 9, 5)
    task = toggle_task(_daily(today), today)
    assert "2026-09-05" in task.completed_periods
    assert snapshot_for(task, today, today).status == TASK_STATUS_COMPLETED
    undone = toggle_task(task, today)
    assert "2026-09-05" not in undone.completed_periods


def test_monthly_rollover_misses_previous_month() -> None:
    task = TaskRecord(
        id="budget",
        title="Presupuesto",
        repeat=TASK_REPEAT_MONTHLY,
        month_day=1,
        created_at="2026-08-01T10:00:00",
        period_cursor="2026-08",
    )
    rolled = rollover_task(task, date(2026, 9, 5))
    assert "2026-08" in rolled.missed_periods
    assert rolled.period_cursor == "2026-09"
    assert snapshot_for(rolled, date(2026, 8, 1), date(2026, 9, 5)).status == TASK_STATUS_MISSED
    assert applies_on(rolled, date(2026, 9, 1))
    assert not applies_on(rolled, date(2026, 9, 5))


def test_one_shot_becomes_overdue_after_due_date() -> None:
    task = TaskRecord(
        id="bill",
        title="Pagar luz",
        repeat=TASK_REPEAT_NONE,
        due_date="2026-09-01",
        created_at="2026-08-20T10:00:00",
        period_cursor="2026-09-01",
    )
    today = date(2026, 9, 5)
    assert snapshot_for(task, today, today).status == TASK_STATUS_OVERDUE
    board = today_board((task,), today)
    assert board[0].status == TASK_STATUS_OVERDUE
    completed = toggle_task(task, today)
    assert snapshot_for(completed, today, today).status == TASK_STATUS_COMPLETED


def test_calendar_marks_dated_and_monthly_but_not_every_daily() -> None:
    tasks = (
        _daily(date(2026, 9, 5)),
        TaskRecord(
            id="dentist",
            title="Dentista",
            due_date="2026-09-12",
            created_at="2026-09-01T10:00:00",
        ),
        TaskRecord(
            id="rent",
            title="Renta",
            repeat=TASK_REPEAT_MONTHLY,
            month_day=5,
            created_at="2026-01-01T10:00:00",
            period_cursor="2026-09",
        ),
    )
    days = marked_days(tasks, 2026, 9)
    assert days == frozenset({5, 12})
    day_tasks = tasks_for_date(tasks, date(2026, 9, 5), date(2026, 9, 5))
    titles = {item.title for item in day_tasks}
    assert titles == {"Agua", "Renta"}


def test_calendar_day_mark_reports_count_and_overdue() -> None:
    from shell.servicios.tareas.logic import calendar_day_mark

    tasks = (
        TaskRecord(
            id="late",
            title="Informe",
            due_date="2026-09-01",
            created_at="2026-08-20T10:00:00",
        ),
        TaskRecord(
            id="rent",
            title="Renta",
            repeat=TASK_REPEAT_MONTHLY,
            month_day=5,
            created_at="2026-01-01T10:00:00",
            period_cursor="2026-09",
        ),
    )
    today = date(2026, 9, 10)
    assert calendar_day_mark(tasks, date(2026, 9, 1), today=today) == (
        1,
        True,
        ("Informe",),
    )
    assert calendar_day_mark(tasks, date(2026, 9, 5), today=today) == (
        1,
        False,
        ("Renta",),
    )
    assert calendar_day_mark(tasks, date(2026, 9, 20), today=today) is None


def test_calendar_day_mark_skips_daily_tasks() -> None:
    from shell.servicios.tareas.logic import calendar_day_mark

    tasks = (_daily(date(2026, 9, 5)),)
    today = date(2026, 9, 5)
    assert calendar_day_mark(tasks, today, today=today) is None


def test_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    original = (
        TaskRecord(
            id="abc",
            title="Leer",
            notes="20 min",
            repeat=TASK_REPEAT_DAILY,
            created_at="2026-09-05T08:00:00",
            period_cursor="2026-09-05",
            completed_periods=("2026-09-04",),
            missed_periods=("2026-09-03",),
        ),
    )
    save_tasks(path, original)
    loaded = load_tasks(path)
    assert loaded == original


def test_complete_task_does_not_uncomplete() -> None:
    today = date(2026, 9, 5)
    task = _daily(today)
    done = complete_task(task, today)
    assert "2026-09-05" in done.completed_periods
    assert complete_task(done, today) == done


def test_service_add_toggle_delete_emits(tmp_path: Path) -> None:
    events: list[object] = []
    bus = EventBus()
    bus.subscribe(TASKS_CHANGED, events.append)
    service = TasksService(bus, path=tmp_path / "tasks.json")
    record = service.add_task("Café", repeat=TASK_REPEAT_DAILY)
    assert record is not None
    assert service.snapshot.pending_today_count == 1
    service.toggle(record.id)
    assert pending_today_count(service.records(), date.today()) == 0
    service.delete(record.id)
    assert service.records() == ()
    assert len(events) == 3


def test_service_updates_existing_task_preserving_id(tmp_path: Path) -> None:
    bus = EventBus()
    service = TasksService(bus, path=tmp_path / "tasks.json")
    created = service.add_task("Borrador", notes="vieja", due_date="2026-09-12")
    assert created is not None

    updated = service.update_task(
        created.id,
        "Final",
        notes="nueva",
        repeat=TASK_REPEAT_MONTHLY,
        month_day=15,
    )

    assert updated is not None
    assert updated.id == created.id
    assert updated.title == "Final"
    assert updated.notes == "nueva"
    assert updated.repeat == TASK_REPEAT_MONTHLY
    assert updated.month_day == 15
    assert updated.due_date is None
    assert load_tasks(tmp_path / "tasks.json") == (updated,)


def test_service_reloads_external_tasks_json_changes(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    bus = EventBus()
    service = TasksService(bus, path=path)
    created = service.add_task("Informe", due_date=date.today().isoformat())
    assert created is not None
    disk = load_tasks(path)
    completed = complete_task(disk[0], date.today())
    save_tasks(path, (completed,))
    later = path.stat().st_mtime_ns + 1
    os.utime(path, ns=(later, later))
    assert service._on_rollover_tick() is True
    assert snapshot_for(service.records()[0], date.today(), date.today()).status == TASK_STATUS_COMPLETED


def test_service_toggle_future_one_shot(tmp_path: Path) -> None:
    bus = EventBus()
    service = TasksService(bus, path=tmp_path / "tasks.json")
    future = date.today() + __import__("datetime").timedelta(days=5)
    record = service.add_task("Informe", due_date=future.isoformat())
    assert record is not None
    service.toggle(record.id, on_date=future)
    snap = snapshot_for(service.records()[0], future, date.today())
    assert snap.status == TASK_STATUS_COMPLETED
    assert future.isoformat() in service.records()[0].completed_periods


def test_service_toggle_future_monthly_uses_occurrence_month(tmp_path: Path) -> None:
    bus = EventBus()
    service = TasksService(bus, path=tmp_path / "tasks.json")
    record = service.add_task("Renta", repeat=TASK_REPEAT_MONTHLY, month_day=1)
    assert record is not None
    target = date(date.today().year + 1, 3, 1)
    service.toggle(record.id, on_date=target)
    key = period_key(TASK_REPEAT_MONTHLY, target)
    assert key in service.records()[0].completed_periods
    service.toggle(record.id, on_date=target)
    assert key not in service.records()[0].completed_periods


def test_store_roundtrips_category_and_priority(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    original = (
        TaskRecord(
            id="tax",
            title="Ensayo",
            due_date="2026-09-20",
            created_at="2026-09-01T10:00:00",
            category_id="universidad",
            priority_id="alta",
        ),
    )
    save_tasks(path, original)
    loaded = load_tasks(path)
    assert loaded == original


def test_completed_snapshots_include_early_one_shot() -> None:
    from shell.servicios.tareas.logic import completed_snapshots

    today = date(2026, 9, 5)
    task = TaskRecord(
        id="early",
        title="Leer",
        due_date="2026-09-12",
        created_at="2026-09-01T10:00:00",
        completed_periods=("2026-09-12",),
    )
    items = completed_snapshots((task,), today)
    assert len(items) == 1
    assert items[0].status == TASK_STATUS_COMPLETED
    assert items[0].occurrence_date == "2026-09-12"


if __name__ == "__main__":
    from pathlib import Path as _Path
    import tempfile

    test_daily_period_key_is_the_calendar_day()
    test_monthly_period_key_is_year_month()
    test_daily_rollover_misses_yesterday_and_resets_today()
    test_daily_rollover_catches_up_after_several_days()
    test_completed_daily_does_not_count_as_missed()
    test_daily_toggle_completes_only_current_period()
    test_monthly_rollover_misses_previous_month()
    test_one_shot_becomes_overdue_after_due_date()
    test_calendar_marks_dated_and_monthly_but_not_every_daily()
    test_calendar_day_mark_reports_count_and_overdue()
    test_calendar_day_mark_skips_daily_tasks()
    test_complete_task_does_not_uncomplete()
    test_completed_snapshots_include_early_one_shot()
    with tempfile.TemporaryDirectory() as folder:
        test_store_roundtrip(_Path(folder))
        test_service_add_toggle_delete_emits(_Path(folder))
        test_service_updates_existing_task_preserving_id(_Path(folder))
        test_service_reloads_external_tasks_json_changes(_Path(folder))
        test_service_toggle_future_one_shot(_Path(folder))
        test_service_toggle_future_monthly_uses_occurrence_month(_Path(folder))
        test_store_roundtrips_category_and_priority(_Path(folder))
    print("tasks tests OK")
