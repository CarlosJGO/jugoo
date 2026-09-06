from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import os
import subprocess
import time

from shell.models import (
    TASK_REPEAT_DAILY,
    TASK_REPEAT_NONE,
    TASK_STATUS_COMPLETED,
    ActiveWindow,
    TaskRecord,
    TaskSnapshot,
)
from shell.eventbus import EventBus
from shell.servicios.sistema.system import ComputeResources
from shell.servicios.tareas.presencia import (
    STATUS_ALIVE,
    STATUS_INACTIVE,
    STATUS_QUIET,
    STATUS_UNKNOWN,
    TaskWatcherBridge,
    WatcherPresence,
)
from shell.servicios.tareas.vigilancia.eventos import (
    KIND_AI_REMINDER,
    KIND_HEARTBEAT,
    KIND_REMINDER,
    TASK_WATCHER_AI_REMINDER,
    TASK_WATCHER_HEARTBEAT,
    TASK_WATCHER_REMINDER,
)
from shell.widgets.tareas.pulse import pulse_amplitude, pulse_duration_ms, pulse_progress_scale
from shell.servicios.tareas.proveedor import LocalTaskProvider
from shell.servicios.tareas.store import save_tasks
from shell.servicios.tareas.vigilancia.config import WatcherConfig
from shell.servicios.tareas.vigilancia.contexto import is_distraction_window
from shell.servicios.tareas.vigilancia.estado import ReminderState
from shell.servicios.tareas.vigilancia.ia import (
    LocalTextGenerator,
    fallback_phrase,
    validate_ai_output,
    _kill_process_group,
)
from shell.servicios.tareas.vigilancia.politica import (
    ActivitySnapshot,
    choose_reminder,
    due_datetime,
)
from shell.servicios.tareas.vigilancia.recursos import (
    ResourceMonitor,
    estimate_model_vram_bytes,
    evaluate_ai_viability,
)
from shell.servicios.tareas.vigilancia.servicio import TaskWatcher
from shell.servicios.tareas.vigilancia.notificaciones import ACTION_DONE, ACTION_SNOOZE_SHORT


def _config(**overrides) -> WatcherConfig:
    values = dict(
        enabled=True,
        poll_interval_sec=45,
        distraction_threshold_sec=20 * 60,
        urgent_window_sec=2 * 60 * 60,
        future_horizon_sec=8 * 60 * 60,
        reminder_cooldown_sec=60 * 60,
        snooze_short_sec=15 * 60,
        snooze_long_sec=60 * 60,
        max_reminders_per_occurrence=4,
        ai_enabled=True,
        resource_monitor_enabled=True,
        minimum_vram_margin_bytes=100,
        maximum_gpu_usage_percent=80.0,
        minimum_available_ram_bytes=1024,
        compute_overhead_bytes=50,
        ai_layer_count=32,
        ai_ngl=99,
        ai_timeout_sec=1,
        ai_context_size=1,
        ai_binary="llama-cli",
        ai_model_path="/tmp/missing-model.gguf",
    )
    values.update(overrides)
    return WatcherConfig(**values)


def _snapshot(
    *,
    ident: str = "task-1",
    title: str = "Terminar informe",
    status: str = "pending",
    due_date: str = "2026-09-05",
    period_key: str = "2026-09-05",
    repeat: str = TASK_REPEAT_NONE,
) -> TaskSnapshot:
    return TaskSnapshot(
        id=ident,
        title=title,
        notes="",
        repeat=repeat,
        due_date=due_date,
        month_day=1,
        status=status,
        period_key=period_key,
        missed_count=0,
        created_at="2026-09-01T10:00:00",
        occurrence_date="2026-09-05",
    )


def _now() -> datetime:
    return datetime(2026, 9, 5, 21, 30, 0)


def _activity(*, distracted: bool = False, seconds: float = 0.0) -> ActivitySnapshot:
    return ActivitySnapshot(
        distracted=distracted,
        distracted_for_sec=seconds,
        app_class="steam" if distracted else "kitty",
        title="Spelunky",
        label="user has been distracted in Steam for 35 minutes" if distracted else "desktop",
    )


class RecordingNotifier:
    def __init__(self) -> None:
        self.bodies: list[str] = []
        self.announces: list[str] = []
        self.opened = 0
        self._next_id = 1
        self.on_action = None

    def start(self, on_action) -> None:
        self.on_action = on_action

    def close(self) -> None:
        return

    def announce(self, kind: str) -> None:
        self.announces.append(kind)

    def notify(self, *, summary, body, urgency, expire_timeout_ms, task_id) -> int:
        self.bodies.append(body)
        notification_id = self._next_id
        self._next_id += 1
        return notification_id

    def open_tasks_panel(self) -> None:
        self.opened += 1


class FakeGenerator:
    def __init__(self, text: str | None = "Ey, acuérdate del informe 👀") -> None:
        self.text = text
        self.calls = 0
        self.prompts: list[str] = []
        self.closed = False

    def generate(self, prompt: str, **_kwargs) -> str | None:
        self.calls += 1
        self.prompts.append(prompt)
        return self.text

    def close(self) -> None:
        self.closed = True


def test_overdue_task_is_eligible() -> None:
    now = _now()
    snapshot = _snapshot(status="overdue", due_date="2026-09-01", period_key="2026-09-01")
    decision = choose_reminder((snapshot,), ReminderState(), _activity(), now=now, config=_config())
    assert decision.should_notify
    assert decision.reason == "overdue"


def test_task_due_within_urgent_window() -> None:
    snapshot = _snapshot()
    now = datetime(2026, 9, 5, 22, 30, 0)
    decision = choose_reminder((snapshot,), ReminderState(), _activity(), now=now, config=_config())
    assert decision.should_notify
    assert decision.reason == "urgent"
    assert due_datetime(snapshot).hour == 23


def test_future_task_outside_horizon_is_quiet() -> None:
    snapshot = _snapshot(due_date="2026-09-12", period_key="2026-09-12")
    now = datetime(2026, 9, 5, 10, 0, 0)
    decision = choose_reminder((snapshot,), ReminderState(), _activity(), now=now, config=_config())
    assert not decision.should_notify
    assert decision.reason == "future"


def test_distraction_raises_relevance_inside_horizon() -> None:
    snapshot = _snapshot()
    now = datetime(2026, 9, 5, 18, 0, 0)
    quiet = choose_reminder((snapshot,), ReminderState(), _activity(), now=now, config=_config())
    assert not quiet.should_notify
    busy = choose_reminder(
        (snapshot,),
        ReminderState(),
        _activity(distracted=True, seconds=35 * 60),
        now=now,
        config=_config(),
    )
    assert busy.should_notify
    assert busy.reason == "distracted"


def test_cooldown_blocks_repeat_spam() -> None:
    snapshot = _snapshot(status="overdue", due_date="2026-09-01", period_key="2026-09-01")
    state = ReminderState()
    now = _now()
    first = choose_reminder((snapshot,), state, _activity(), now=now, config=_config())
    assert first.should_notify
    state.mark_notified(snapshot.id, snapshot.period_key, now.timestamp())
    again = choose_reminder((snapshot,), state, _activity(), now=now + timedelta(minutes=10), config=_config())
    assert not again.should_notify
    assert again.reason == "cooldown"


def test_snooze_is_respected() -> None:
    snapshot = _snapshot(status="overdue", due_date="2026-09-01", period_key="2026-09-01")
    state = ReminderState()
    now = _now()
    state.snooze(snapshot.id, now.timestamp() + 15 * 60)
    decision = choose_reminder((snapshot,), state, _activity(), now=now, config=_config())
    assert not decision.should_notify
    assert decision.reason == "snooze"


def test_completed_task_is_forgotten() -> None:
    snapshot = _snapshot(status=TASK_STATUS_COMPLETED)
    state = ReminderState()
    state.mark_notified(snapshot.id, snapshot.period_key, _now().timestamp())
    decision = choose_reminder((snapshot,), state, _activity(), now=_now(), config=_config())
    assert not decision.should_notify
    assert snapshot.id not in state._items


def test_daily_period_change_resets_reminder_count() -> None:
    snapshot = _snapshot(
        repeat=TASK_REPEAT_DAILY,
        due_date=None,
        period_key="2026-09-05",
        status="pending",
    )
    state = ReminderState()
    state.mark_notified(snapshot.id, "2026-09-04", _now().timestamp())
    memory = state.align_period(snapshot.id, snapshot.period_key)
    assert memory.reminder_count == 0
    assert memory.last_notified_at == 0.0


def test_local_provider_reloads_only_when_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(
        path,
        (
            TaskRecord(
                id="abc",
                title="Leer",
                due_date="2026-09-05",
                created_at="2026-09-05T08:00:00",
                period_cursor="2026-09-05",
            ),
        ),
    )
    provider = LocalTaskProvider(path)
    assert provider.reload_if_changed() is False
    later = path.stat().st_mtime_ns + 1
    save_tasks(
        path,
        (
            TaskRecord(
                id="abc",
                title="Leer más",
                due_date="2026-09-05",
                created_at="2026-09-05T08:00:00",
                period_cursor="2026-09-05",
            ),
        ),
    )
    os.utime(path, ns=(later, later))
    assert provider.reload_if_changed() is True
    assert provider.records()[0].title == "Leer más"


def test_local_provider_complete_writes_same_store(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    today = datetime.now().date()
    save_tasks(
        path,
        (
            TaskRecord(
                id="bill",
                title="Pagar luz",
                due_date=today.isoformat(),
                created_at=today.isoformat() + "T08:00:00",
                period_cursor=today.isoformat(),
            ),
        ),
    )
    provider = LocalTaskProvider(path)
    assert provider.complete("bill", today=today) is True
    assert provider.complete("bill", today=today) is False
    board = provider.board(today)
    assert board[0].status == TASK_STATUS_COMPLETED


def test_ai_viability_ok_when_resources_are_free(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 1000)
    resources = ComputeResources(
        vram_used_bytes=1_000,
        vram_total_bytes=10_000_000,
        gpu_usage_percent=12.0,
        ram_available_bytes=8_000_000,
        ram_total_bytes=16_000_000,
    )
    result = evaluate_ai_viability(resources, config=_config(ai_model_path=str(model)))
    assert result.viable
    assert result.reason == "ok"


def test_ai_fallback_when_vram_is_tight(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 8_000)
    resources = ComputeResources(
        vram_used_bytes=7_000,
        vram_total_bytes=8_000,
        gpu_usage_percent=10.0,
        ram_available_bytes=8_000,
        ram_total_bytes=16_000,
    )
    result = evaluate_ai_viability(resources, config=_config(ai_model_path=str(model)))
    assert not result.viable
    assert result.reason == "vram"


def test_ai_fallback_when_gpu_is_busy(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)
    resources = ComputeResources(
        vram_used_bytes=100,
        vram_total_bytes=10_000_000,
        gpu_usage_percent=97.0,
        ram_available_bytes=8_000_000,
        ram_total_bytes=16_000_000,
    )
    result = evaluate_ai_viability(resources, config=_config(ai_model_path=str(model)))
    assert not result.viable
    assert result.reason == "gpu_busy"


def test_ai_fallback_when_ram_is_low(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)
    resources = ComputeResources(
        vram_used_bytes=100,
        vram_total_bytes=10_000_000,
        gpu_usage_percent=10.0,
        ram_available_bytes=10,
        ram_total_bytes=16_000,
    )
    result = evaluate_ai_viability(resources, config=_config(ai_model_path=str(model)))
    assert not result.viable
    assert result.reason == "ram"


def test_ai_fallback_when_monitor_fails(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)

    def boom() -> ComputeResources:
        raise RuntimeError("drm missing")

    monitor = ResourceMonitor(reader=boom)
    result = monitor.viability(_config(ai_model_path=str(model)))
    assert not result.viable
    assert result.reason == "unreliable"


def test_ai_fallback_when_vram_unknown(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)
    resources = ComputeResources(gpu_usage_percent=5.0, ram_available_bytes=8_000)
    result = evaluate_ai_viability(resources, config=_config(ai_model_path=str(model)))
    assert not result.viable
    assert result.reason == "unreliable"


def test_estimate_does_not_use_raw_file_size_alone(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 1000)
    estimated = estimate_model_vram_bytes(
        model,
        context_size=1024,
        ngl=99,
        layer_count=32,
        overhead_bytes=200,
    )
    assert estimated is not None
    assert estimated > 1000


def test_validate_accepts_short_spanish_phrase() -> None:
    assert validate_ai_output('Ey, acuérdate del informe, vence esta noche 👀\n') == (
        "Ey, acuérdate del informe, vence esta noche 👀"
    )


def test_validate_rejects_empty_json_and_meta() -> None:
    assert validate_ai_output("") is None
    assert validate_ai_output('{"reminder":"hi"}') is None
    assert validate_ai_output("Sure, here is a reminder for you.") is None
    assert validate_ai_output("uno\n\ndos\n\ntres") is None
    assert validate_ai_output("palabra " * 40) is None


def test_llama_missing_binary_returns_none(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    generator = LocalTextGenerator(
        _config(ai_model_path=str(model), ai_binary="llama-cli"),
        which=lambda _name: None,
    )
    assert generator.generate("hola") is None


def test_llama_timeout_kills_child() -> None:
    started: list[subprocess.Popen] = []

    def fake_popen(*_args, **_kwargs):
        proc = subprocess.Popen(
            ["sleep", "30"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        started.append(proc)
        return proc

    generator = LocalTextGenerator(
        _config(ai_timeout_sec=0.2, ai_model_path="/etc/hostname"),
        which=lambda _name: "/usr/bin/true",
        popen=fake_popen,
    )
    assert generator.generate("hola") is None
    assert started
    time.sleep(0.1)
    assert started[0].poll() is not None


def test_llama_valid_and_invalid_output(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    class _Proc:
        def __init__(self, stdout: str, code: int = 0) -> None:
            self._stdout = stdout
            self.returncode = code
            self.pid = 0

        def communicate(self, timeout=None):
            return self._stdout, ""

        def poll(self):
            return self.returncode

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            return self.returncode

    def good(*_args, **_kwargs):
        return _Proc("Ey, no te olvides del informe.\n")

    def bad(*_args, **_kwargs):
        return _Proc('{"ok": true}')

    ok = LocalTextGenerator(_config(ai_model_path=str(model)), which=lambda _n: "llama-cli", popen=good)
    fail = LocalTextGenerator(_config(ai_model_path=str(model)), which=lambda _n: "llama-cli", popen=bad)
    assert ok.generate("x") == "Ey, no te olvides del informe."
    assert fail.generate("x") is None


def test_kill_process_group_reaps_sleep() -> None:
    proc = subprocess.Popen(
        ["sleep", "30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _kill_process_group(proc)
    assert proc.poll() is not None


def test_firefox_is_not_always_a_distraction() -> None:
    plain = ActiveWindow("0x1", "firefox", "Firefox", "Gmail — Mozilla Firefox", "firefox")
    video = ActiveWindow("0x2", "firefox", "Firefox", "YouTube — Mozilla Firefox", "firefox")
    steam = ActiveWindow("0x3", "steam", "Steam", "Spelunky", "steam")
    assert is_distraction_window(plain) is False
    assert is_distraction_window(video) is True
    assert is_distraction_window(steam) is True


def test_watcher_uses_fallback_when_ai_is_not_viable(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(
        path,
        (
            TaskRecord(
                id="bill",
                title="Pagar luz",
                due_date="2026-09-01",
                created_at="2026-08-20T10:00:00",
                period_cursor="2026-09-01",
            ),
        ),
    )
    notifier = RecordingNotifier()
    generator = FakeGenerator()
    watcher = TaskWatcher(
        config=_config(),
        provider=LocalTaskProvider(path),
        resource_monitor=ResourceMonitor(
            reader=lambda: ComputeResources(gpu_usage_percent=99.0)
        ),
        notifier=notifier,
        generator=generator,
        clock=_now,
    )
    watcher._context.observe(None)
    watcher._tick()
    assert notifier.bodies
    assert generator.calls == 0
    assert "luz" in notifier.bodies[0] or "tarea" in notifier.bodies[0].casefold()


def test_watcher_uses_ai_when_resources_allow(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)
    save_tasks(
        path,
        (
            TaskRecord(
                id="bill",
                title="Pagar luz",
                due_date="2026-09-01",
                created_at="2026-08-20T10:00:00",
                period_cursor="2026-09-01",
            ),
        ),
    )
    notifier = RecordingNotifier()
    generator = FakeGenerator("Ey, acuérdate de pagar la luz 👀")
    watcher = TaskWatcher(
        config=_config(ai_model_path=str(model)),
        provider=LocalTaskProvider(path),
        resource_monitor=ResourceMonitor(
            reader=lambda: ComputeResources(
                vram_used_bytes=100,
                vram_total_bytes=50_000_000,
                gpu_usage_percent=8.0,
                ram_available_bytes=9_000_000,
                ram_total_bytes=16_000_000,
            )
        ),
        notifier=notifier,
        generator=generator,
        clock=_now,
    )
    watcher._tick()
    if watcher._ai_thread is not None:
        watcher._ai_thread.join(timeout=1)
    assert generator.calls == 1
    assert notifier.bodies == ["Ey, acuérdate de pagar la luz 👀"]


def test_watcher_done_and_snooze_actions(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    today = datetime.now().date()
    due = today - timedelta(days=2)
    save_tasks(
        path,
        (
            TaskRecord(
                id="bill",
                title="Pagar luz",
                due_date=due.isoformat(),
                created_at=due.isoformat() + "T08:00:00",
                period_cursor=due.isoformat(),
            ),
        ),
    )
    notifier = RecordingNotifier()
    watcher = TaskWatcher(
        config=_config(ai_enabled=False),
        provider=LocalTaskProvider(path),
        notifier=notifier,
        generator=FakeGenerator(),
        clock=datetime.now,
    )
    watcher._tick()
    assert notifier.bodies
    notification_id = 1
    watcher._on_notification_action(notification_id, ACTION_SNOOZE_SHORT)
    decision = watcher.evaluate_once()
    assert not decision.should_notify
    watcher._on_notification_action(notification_id, ACTION_DONE)
    records = watcher._provider.records()
    assert due.isoformat() in records[0].completed_periods


def test_fallback_phrases_vary() -> None:
    first = fallback_phrase("informe", seed="a")
    second = fallback_phrase("informe", seed="bbbb")
    assert "informe" in first
    assert first != "Tienes una tarea pendiente."
    assert first != second or fallback_phrase("otra", seed="c") != first


def test_watcher_emits_heartbeat_on_successful_quiet_tick(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    notifier = RecordingNotifier()
    received: list[str] = []
    watcher = TaskWatcher(
        config=_config(ai_enabled=False),
        provider=LocalTaskProvider(path),
        notifier=notifier,
        generator=FakeGenerator(),
        clock=_now,
    )
    watcher._event_bus.subscribe(TASK_WATCHER_HEARTBEAT, received.append)
    assert watcher._on_tick() is True
    assert received == [KIND_HEARTBEAT]
    assert notifier.announces == [KIND_HEARTBEAT]
    assert notifier.bodies == []


def test_heartbeat_event_name_is_stable() -> None:
    assert TASK_WATCHER_HEARTBEAT == "task_watcher_heartbeat"
    assert TASK_WATCHER_REMINDER == "task_watcher_reminder"
    assert TASK_WATCHER_AI_REMINDER == "task_watcher_ai_reminder"


def test_watcher_emits_reminder_instead_of_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(
        path,
        (
            TaskRecord(
                id="bill",
                title="Pagar luz",
                due_date="2026-09-01",
                created_at="2026-08-20T10:00:00",
                period_cursor="2026-09-01",
            ),
        ),
    )
    notifier = RecordingNotifier()
    heartbeats: list[str] = []
    reminders: list[str] = []
    watcher = TaskWatcher(
        config=_config(ai_enabled=False),
        provider=LocalTaskProvider(path),
        notifier=notifier,
        generator=FakeGenerator(),
        clock=_now,
    )
    watcher._event_bus.subscribe(TASK_WATCHER_HEARTBEAT, heartbeats.append)
    watcher._event_bus.subscribe(TASK_WATCHER_REMINDER, reminders.append)
    watcher._on_tick()
    assert reminders == [KIND_REMINDER]
    assert heartbeats == []
    assert notifier.bodies


def test_watcher_emits_ai_reminder_kind(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)
    save_tasks(
        path,
        (
            TaskRecord(
                id="bill",
                title="Pagar luz",
                due_date="2026-09-01",
                created_at="2026-08-20T10:00:00",
                period_cursor="2026-09-01",
            ),
        ),
    )
    notifier = RecordingNotifier()
    kinds: list[str] = []
    watcher = TaskWatcher(
        config=_config(ai_model_path=str(model)),
        provider=LocalTaskProvider(path),
        resource_monitor=ResourceMonitor(
            reader=lambda: ComputeResources(
                vram_used_bytes=100,
                vram_total_bytes=50_000_000,
                gpu_usage_percent=8.0,
                ram_available_bytes=9_000_000,
                ram_total_bytes=16_000_000,
            )
        ),
        notifier=notifier,
        generator=FakeGenerator("Ey, acuérdate de pagar la luz 👀"),
        clock=_now,
    )
    watcher._event_bus.subscribe(TASK_WATCHER_AI_REMINDER, kinds.append)
    watcher._on_tick()
    if watcher._ai_thread is not None:
        watcher._ai_thread.join(timeout=1)
    assert kinds == [KIND_AI_REMINDER]


def test_presence_starts_unknown_until_real_heartbeat() -> None:
    presence = WatcherPresence()
    assert presence.status == STATUS_UNKNOWN
    assert presence.last_heartbeat_at is None
    presence.note(KIND_HEARTBEAT, 42.0)
    assert presence.last_heartbeat_at == 42.0
    assert presence.status == STATUS_ALIVE


def test_presence_stale_becomes_quiet_then_inactive() -> None:
    presence = WatcherPresence()
    presence.note(KIND_REMINDER, 1.0)
    assert presence.mark_quiet() is True
    assert presence.status == STATUS_QUIET
    assert presence.mark_inactive() is True
    assert presence.status == STATUS_INACTIVE
    assert presence.mark_quiet() is False


def test_widget_eventbus_updates_presence_and_pulse_kind() -> None:
    presence = WatcherPresence()
    pulses: list[str] = []
    bus = EventBus()

    def on_pulse(kind: object) -> None:
        pulses.append(presence.note(str(kind), 100.0))

    bus.subscribe(TASK_WATCHER_HEARTBEAT, on_pulse)
    bus.subscribe(TASK_WATCHER_REMINDER, on_pulse)
    bus.emit(TASK_WATCHER_HEARTBEAT, KIND_HEARTBEAT)
    assert presence.last_heartbeat_at == 100.0
    assert presence.status == STATUS_ALIVE
    bus.emit(TASK_WATCHER_REMINDER, KIND_REMINDER)
    assert pulses == [KIND_HEARTBEAT, KIND_REMINDER]


def test_bridge_maps_dbus_kinds_onto_eventbus() -> None:
    bus = EventBus()
    names: list[tuple[str, str]] = []
    bus.subscribe(TASK_WATCHER_HEARTBEAT, lambda kind: names.append((TASK_WATCHER_HEARTBEAT, kind)))
    bus.subscribe(TASK_WATCHER_REMINDER, lambda kind: names.append((TASK_WATCHER_REMINDER, kind)))
    bus.subscribe(TASK_WATCHER_AI_REMINDER, lambda kind: names.append((TASK_WATCHER_AI_REMINDER, kind)))
    bridge = TaskWatcherBridge(bus)
    bridge.dispatch(KIND_HEARTBEAT)
    bridge.dispatch(KIND_REMINDER)
    bridge.dispatch(KIND_AI_REMINDER)
    assert names == [
        (TASK_WATCHER_HEARTBEAT, KIND_HEARTBEAT),
        (TASK_WATCHER_REMINDER, KIND_REMINDER),
        (TASK_WATCHER_AI_REMINDER, KIND_AI_REMINDER),
    ]


def test_reminder_pulse_is_stronger_than_heartbeat() -> None:
    assert pulse_amplitude(KIND_REMINDER) > pulse_amplitude(KIND_HEARTBEAT)
    assert pulse_amplitude(KIND_AI_REMINDER) == pulse_amplitude(KIND_REMINDER)
    peak_heartbeat = pulse_progress_scale(0.5, pulse_amplitude(KIND_HEARTBEAT))
    peak_reminder = pulse_progress_scale(0.5, pulse_amplitude(KIND_REMINDER))
    assert peak_heartbeat == 1.0 + pulse_amplitude(KIND_HEARTBEAT)
    assert peak_reminder == 1.0 + pulse_amplitude(KIND_REMINDER)
    assert pulse_progress_scale(0.0, 0.12) == 1.0
    assert pulse_progress_scale(1.0, 0.12) == 1.0


def test_pulse_playback_is_a_short_one_shot() -> None:
    duration = pulse_duration_ms(KIND_HEARTBEAT)
    elapsed = 0
    ticks = 0
    running = True
    while running:
        elapsed += 16
        ticks += 1
        running = elapsed < duration
        assert ticks < 40
    assert duration <= 300
    assert not running
    assert ticks == (duration + 15) // 16


def test_pulse_icon_one_shot_timer_stops() -> None:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception:
        return
    if not Gtk.init_check()[0]:
        return
    from shell.widgets.tareas.tasks_icon import TasksPulseIcon

    icon = TasksPulseIcon(16)
    icon.pulse(KIND_REMINDER)
    assert icon.source_id != 0
    icon._elapsed_ms = pulse_duration_ms(KIND_REMINDER)
    assert icon._tick() is False
    assert icon.source_id == 0
    assert icon.scale == 1.0
    icon.stop()


def test_watcher_lock_prevents_a_second_instance(tmp_path: Path) -> None:
    from shell.servicios.tareas.vigilancia.sesion import (
        acquire_watcher_lock,
        release_watcher_lock,
    )

    lock = tmp_path / "task-watcher.lock"
    first = acquire_watcher_lock(lock)
    second = acquire_watcher_lock(lock)
    assert first is not None
    assert second is None
    release_watcher_lock(first)


if __name__ == "__main__":
    from pathlib import Path as _Path
    import inspect
    import tempfile

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        parameters = inspect.signature(test).parameters
        if "tmp_path" in parameters:
            with tempfile.TemporaryDirectory() as folder:
                test(_Path(folder))
        else:
            test()
        print(f"ok {name}")
    print("task watcher tests OK")
