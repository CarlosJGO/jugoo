from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import inspect
import threading
import time

from shell.models import (
    TASK_REPEAT_DAILY,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_PENDING,
    TaskRecord,
    TaskSnapshot,
    TasksSnapshot,
)
from shell.servicios.sistema.system import ComputeResources
from shell.servicios.tareas.briefing import (
    StartupTaskBriefing,
    build_briefing_prompt,
    collect_briefing_facts,
    fallback_briefing_text,
    facts_from_provider,
)
from shell.servicios.tareas.proveedor import LocalTaskProvider
from shell.servicios.tareas.store import save_tasks
from shell.servicios.tareas.vigilancia.config import WatcherConfig
from shell.servicios.tareas.vigilancia.ia import LocalTextGenerator, validate_ai_output
from shell.servicios.tareas.vigilancia.recursos import ResourceMonitor
from shell.servicios.tareas.vigilancia.sesion import (
    TASK_WATCHER_UNIT,
    acquire_watcher_lock,
    ensure_task_watcher_service,
    release_watcher_lock,
)
from shell.servicios.tareas.vigilancia.servicio import TaskWatcher


def _config(tmp_path: Path, **overrides) -> WatcherConfig:
    model = tmp_path / "model.gguf"
    if not model.exists():
        model.write_bytes(b"x" * 100)
    values = dict(
        enabled=True,
        poll_interval_sec=45,
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
        ai_model_path=str(model),
        briefing_enabled=True,
        briefing_max_tokens=80,
    )
    values.update(overrides)
    return WatcherConfig(**values)


def _free_resources() -> ResourceMonitor:
    return ResourceMonitor(
        reader=lambda: ComputeResources(
            vram_used_bytes=100,
            vram_total_bytes=50_000_000,
            gpu_usage_percent=8.0,
            ram_available_bytes=9_000_000,
            ram_total_bytes=16_000_000,
        )
    )


def _busy_resources() -> ResourceMonitor:
    return ResourceMonitor(
        reader=lambda: ComputeResources(
            vram_used_bytes=100,
            vram_total_bytes=50_000_000,
            gpu_usage_percent=99.0,
            ram_available_bytes=9_000_000,
            ram_total_bytes=16_000_000,
        )
    )


class _Generator:
    def __init__(self, text: str | None = "Hey. Tienes tres cosas pendientes.") -> None:
        self.text = text
        self.calls = 0
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []
        self.closed = False

    def generate(self, prompt: str, **kwargs) -> str | None:
        self.calls += 1
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        return self.text

    def close(self) -> None:
        self.closed = True


def _record(task_id: str, title: str, due: date, repeat: str = "none") -> TaskRecord:
    return TaskRecord(
        id=task_id,
        title=title,
        repeat=repeat,
        due_date=due.isoformat(),
        created_at=due.isoformat() + "T08:00:00",
        period_cursor=due.isoformat(),
    )


def test_facts_use_real_task_state_without_ids(tmp_path: Path) -> None:
    today = date.today()
    overdue = today - timedelta(days=2)
    path = tmp_path / "tasks.json"
    save_tasks(
        path,
        (
            _record("secret-id", "Estudiar X", today),
            _record("other-id", "Pagar luz", overdue),
        ),
    )
    facts = facts_from_provider(LocalTaskProvider(path), today=today)
    assert facts.pending_today == 2
    assert facts.overdue == 1
    assert "Estudiar X" in facts.pending_titles
    assert facts.overdue_titles == ("Pagar luz",)
    prompt = build_briefing_prompt(facts)
    assert "tareas pendientes hoy: 2" in prompt
    assert "tareas vencidas: 1" in prompt
    assert "Estudiar X" in prompt
    assert "Pagar luz" in prompt
    assert prompt.count("Estudiar X") == 1
    assert "Estudiar X (" not in prompt
    assert "No muestres fechas" in prompt
    assert "Hola, ¿cómo te va?" in prompt
    assert "secret-id" not in prompt
    assert "other-id" not in prompt
    assert "tasks.json" not in prompt
    assert str(path) not in prompt


def test_empty_board_still_asks_llama(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    bodies: list[str] = []
    generator = _Generator("Todo tranquilo. No tienes nada pendiente.")
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=bodies.append,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: "llama-cli",
    )
    assert briefing.run() == "ai"
    assert generator.calls == 1
    assert "tareas pendientes hoy: 0" in generator.prompts[0]
    assert "tareas vencidas: 0" in generator.prompts[0]
    assert bodies == ["Todo tranquilo. No tienes nada pendiente."]
    assert bodies[0] != fallback_briefing_text(facts_from_provider(LocalTaskProvider(path)))


def test_briefing_with_tasks_passes_summary(tmp_path: Path) -> None:
    today = date.today()
    path = tmp_path / "tasks.json"
    save_tasks(path, (_record("a", "Estudiar X", today),))
    generator = _Generator("Hey. Tienes una cosa para hoy: estudiar X.")
    bodies: list[str] = []
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=bodies.append,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: "llama-cli",
    )
    assert briefing.run() == "ai"
    assert "Estudiar X" in generator.prompts[0]
    assert "tareas pendientes hoy: 1" in generator.prompts[0]
    assert bodies[0].startswith("Hey.")


def test_briefing_runs_only_once(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    generator = _Generator()
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=lambda _body: None,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: "llama-cli",
    )
    assert briefing.run() == "ai"
    assert briefing.run() == "already"
    assert generator.calls == 1
    assert briefing.schedule() is False


def test_briefing_schedule_does_not_block_ui(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    started = threading.Event()
    release = threading.Event()

    class Slow(_Generator):
        def generate(self, prompt: str, **kwargs) -> str | None:
            started.set()
            assert release.wait(timeout=2)
            return super().generate(prompt, **kwargs)

    generator = Slow()
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=lambda _body: None,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: "llama-cli",
    )
    began = time.monotonic()
    assert briefing.schedule() is False
    assert time.monotonic() - began < 0.2
    assert started.wait(timeout=1)
    assert generator.calls == 0
    release.set()
    assert briefing._thread is not None
    briefing._thread.join(timeout=2)
    assert generator.calls == 1


def test_briefing_fallback_when_ai_not_viable(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    generator = _Generator()
    bodies: list[str] = []
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=bodies.append,
        resource_monitor=_busy_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: "llama-cli",
    )
    assert briefing.run() == "fallback"
    assert generator.calls == 0
    assert bodies == ["Todo limpio. No tienes tareas pendientes."]


def test_briefing_fallback_when_model_missing(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    generator = _Generator()
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=lambda _body: None,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path, ai_model_path=str(tmp_path / "missing.gguf")),
        which=lambda _name: "llama-cli",
    )
    assert briefing.run() == "fallback"
    assert generator.calls == 0


def test_briefing_fallback_when_binary_missing(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    generator = _Generator()
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=lambda _body: None,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: None,
    )
    assert briefing.run() == "fallback"
    assert generator.calls == 0


def test_briefing_fallback_when_output_empty_or_invalid(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    save_tasks(path, ())
    bodies: list[str] = []
    generator = _Generator(None)
    briefing = StartupTaskBriefing(
        provider=LocalTaskProvider(path),
        notify=bodies.append,
        resource_monitor=_free_resources(),
        generator=generator,
        config=_config(tmp_path),
        which=lambda _name: "llama-cli",
    )
    assert briefing.run() == "fallback"
    assert generator.calls == 1
    assert bodies == ["Todo limpio. No tienes tareas pendientes."]


def test_llama_empty_and_invalid_output(tmp_path: Path) -> None:
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

    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    def empty(*_args, **_kwargs):
        return _Proc("")

    def invalid(*_args, **_kwargs):
        return _Proc("Sure, here is a reminder for you.")

    generator = LocalTextGenerator(
        _config(tmp_path, ai_model_path=str(model)),
        which=lambda _n: "llama-cli",
        popen=empty,
    )
    assert generator.generate("hola") is None
    assert generator._proc is None
    generator = LocalTextGenerator(
        _config(tmp_path, ai_model_path=str(model)),
        which=lambda _n: "llama-cli",
        popen=invalid,
    )
    assert generator.generate("hola") is None
    assert generator._proc is None


def test_llama_missing_model_returns_none(tmp_path: Path) -> None:
    generator = LocalTextGenerator(
        _config(tmp_path, ai_model_path=str(tmp_path / "nope.gguf")),
        which=lambda _n: "llama-cli",
    )
    assert generator.generate("hola") is None


def test_validate_briefing_allows_two_sentences() -> None:
    text = validate_ai_output(
        "Hey. Tienes tres pendientes y una ya va tarde.\nEmpieza por esa.",
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text is not None
    assert "tres pendientes" in text


def test_validate_briefing_keeps_last_lines_after_logs() -> None:
    raw = "load_backend: cuda\ninit: done\nHey. Todo limpio por aquí.\nAprovecha el día."
    text = validate_ai_output(
        raw,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text is not None
    assert "Todo limpio" in text


def test_validate_extracts_spanish_from_llama_cli_banner() -> None:
    raw = (
        "\n\nLoading model... \n\n"
        "build      : b10809\n"
        "model      : /tmp/x.gguf\n"
        "available commands:\n"
        "/exit or Ctrl+C     stop or exit\n"
        "/regen              regenerate the last response\n"
        "> <|begin_of_text|>system stuff\n"
        "Buenos días. Tienes una pendiente: estudiar.\n"
        "[ Prompt: 393,0 t/s | Generation: 32,2 t/s ]\n"
        "Exiting...\n"
    )
    text = validate_ai_output(
        raw,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text is not None
    assert "pendiente" in text.casefold()
    assert "t/s" not in text
    assert "Exiting" not in text
    footer = "[ Prompt: 387,3 t/s | Generation: 32,2 t/s ] Exiting..."
    assert validate_ai_output(footer, max_chars=280, max_words=48, max_lines=2, join_lines=True) is None
    mixed = "Hey. Tienes una pendiente para hoy.\n" + footer
    text = validate_ai_output(
        mixed,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text == "Hey. Tienes una pendiente para hoy."


def test_validate_strips_truncated_prompt_echo() -> None:
    raw = (
        "Sé natural, breve y útil. ... (truncated) Hola, como vas? "
        'Tienes 1 tarea pendiente para hoy, "Dejar de proyectarse".\n'
        "[ Prompt: 393,0 t/s | Generation: 32,2 t/s ]\n"
        "Exiting...\n"
    )
    text = validate_ai_output(
        raw,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text is not None
    assert "Sé natural" not in text
    assert "(truncated)" not in text
    assert "Dejar de proyectarse" in text
    mixed_instruction = (
        "Saluda brevemente al usuario y resume su estado de tareas. "
        "¡Hola! Tienes una tarea pendiente para hoy: Dejar de proyectarse."
    )
    text = validate_ai_output(
        mixed_instruction,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text == "¡Hola! Tienes una tarea pendiente para hoy: Dejar de proyectarse."
    context_echo = (
        '- recurrencia: cada día ¡Hola! Tienes una tarea pendiente para hoy, '
        'que es "Dejar de proyectarse".'
    )
    text = validate_ai_output(
        context_echo,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text is not None
    assert not text.startswith("-")
    assert "recurrencia" not in text
    assert "Dejar de proyectarse" in text
    title_echo = (
        'Dejar de proyectarse Hola, buenos días. Tienes una tarea pendiente hoy: '
        '"Dejar de proyectarse", que vence hoy.'
    )
    text = validate_ai_output(
        title_echo,
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text is not None
    assert text.startswith("Hola,")
    assert not text.startswith("Dejar de proyectarse Hola")


def test_validate_strips_title_before_buenos_dias() -> None:
    text = validate_ai_output(
        "Dejar de proyectarse Buenos días, tienes una tarea para hoy.",
        max_chars=280,
        max_words=48,
        max_lines=2,
        join_lines=True,
    )
    assert text == "Buenos días, tienes una tarea para hoy."


def test_fallback_is_emergency_only() -> None:
    empty = collect_briefing_facts(TasksSnapshot(today=date.today().isoformat()))
    loaded = collect_briefing_facts(
        TasksSnapshot(
            today=date.today().isoformat(),
            tasks=(
                TaskSnapshot(
                    id="a",
                    title="Estudiar X",
                    notes="",
                    repeat="none",
                    due_date=date.today().isoformat(),
                    month_day=1,
                    status=TASK_STATUS_PENDING,
                    period_key=date.today().isoformat(),
                    missed_count=0,
                    created_at="",
                    occurrence_date=date.today().isoformat(),
                ),
                TaskSnapshot(
                    id="b",
                    title="Pagar luz",
                    notes="",
                    repeat="none",
                    due_date=(date.today() - timedelta(days=1)).isoformat(),
                    month_day=1,
                    status=TASK_STATUS_OVERDUE,
                    period_key=(date.today() - timedelta(days=1)).isoformat(),
                    missed_count=0,
                    created_at="",
                    occurrence_date=date.today().isoformat(),
                ),
            ),
            overdue_count=1,
            pending_today_count=2,
        )
    )
    assert fallback_briefing_text(empty) == "Todo limpio. No tienes tareas pendientes."
    assert "2 tareas pendientes" in fallback_briefing_text(loaded)
    assert "vencida" in fallback_briefing_text(loaded)


def test_ensure_watcher_uses_systemd_not_a_python_spawn(tmp_path: Path) -> None:
    unit = tmp_path / "jugoo-task-watcher.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stderr = ""

    def runner(command, **_kwargs):
        calls.append(list(command))
        return Result()

    assert ensure_task_watcher_service(runner=runner, unit_path=unit) == "started"
    assert calls == [["systemctl", "--user", "enable", "--now", TASK_WATCHER_UNIT]]
    joined = " ".join(calls[0])
    assert "python" not in joined
    assert "--task-watcher" not in joined


def test_watcher_lock_is_exclusive(tmp_path: Path) -> None:
    lock = tmp_path / "task-watcher.lock"
    first = acquire_watcher_lock(lock)
    second = acquire_watcher_lock(lock)
    assert first is not None
    assert second is None
    release_watcher_lock(first)
    third = acquire_watcher_lock(lock)
    assert third is not None
    release_watcher_lock(third)


def test_watcher_sleeps_between_ticks_instead_of_busy_loop() -> None:
    source = inspect.getsource(TaskWatcher.run)
    assert "timeout_add_seconds" in source
    assert "_on_startup_tick" in source
    assert "while True" not in source
    startup = inspect.getsource(TaskWatcher._on_startup_tick)
    assert "return False" in startup
    config = WatcherConfig()
    assert config.poll_interval_sec == 45


def test_briefing_does_not_construct_a_watcher() -> None:
    source = inspect.getsource(StartupTaskBriefing)
    assert "TaskWatcher(" not in source
    assert "run_task_watcher" not in source


def test_shell_starts_watcher_via_systemd_only() -> None:
    app_source = Path(__file__).resolve().parents[1] / "app.py"
    text = app_source.read_text(encoding="utf-8")
    assert "ensure_task_watcher_service" in text
    assert "StartupTaskBriefing" in text
    assert "Popen" not in text
    assert "subprocess" not in text


def test_daily_recurrence_hint_is_included() -> None:
    today = date.today()
    facts = collect_briefing_facts(
        TasksSnapshot(
            today=today.isoformat(),
            tasks=(
                TaskSnapshot(
                    id="water",
                    title="Agua",
                    notes="ignored",
                    repeat=TASK_REPEAT_DAILY,
                    due_date=None,
                    month_day=today.day,
                    status=TASK_STATUS_PENDING,
                    period_key=today.isoformat(),
                    missed_count=0,
                    created_at="",
                    occurrence_date=today.isoformat(),
                ),
            ),
            pending_today_count=1,
        )
    )
    prompt = build_briefing_prompt(facts)
    assert "recurrencia: cada día" in prompt
    assert "ignored" not in prompt


def test_briefing_prompt_does_not_format_task_as_title_and_date() -> None:
    today = date.today()
    facts = collect_briefing_facts(
        TasksSnapshot(
            today=today.isoformat(),
            tasks=(
                TaskSnapshot(
                    id="task",
                    title="Dejar de proyectarse",
                    notes="",
                    repeat="none",
                    due_date=today.isoformat(),
                    month_day=today.day,
                    status=TASK_STATUS_PENDING,
                    period_key=today.isoformat(),
                    missed_count=0,
                    created_at="",
                    occurrence_date=today.isoformat(),
                ),
            ),
            pending_today_count=1,
        )
    )
    prompt = build_briefing_prompt(facts)
    assert "Dejar de proyectarse (" not in prompt
    assert "No muestres fechas" in prompt
    assert "no uses una charla genérica" in prompt


if __name__ == "__main__":
    from pathlib import Path as _Path
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
    print("startup briefing tests OK")
