"""One-shot Task startup briefing. Separate from the resident Task Watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import shutil
import threading
from typing import Callable

from ...models import (
    TASK_REPEAT_DAILY,
    TASK_REPEAT_MONTHLY,
    TASK_REPEAT_NONE,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_PENDING,
    TaskSnapshot,
    TasksSnapshot,
)
from .logic import overdue_count, pending_today_count, upcoming_tasks
from .proveedor import LocalTaskProvider
from .vigilancia.config import WatcherConfig
from .vigilancia.ia import generate_briefing_text, LocalTextGenerator
from .vigilancia.recursos import ResourceMonitor

try:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
except (ImportError, ValueError):
    GLib = None  # type: ignore[assignment]


NotifyFn = Callable[[str], None]
WhichFn = Callable[[str], str | None]
_MAX_TITLES = 4
_REPEAT_HINTS = {
    TASK_REPEAT_DAILY: "cada día",
    TASK_REPEAT_MONTHLY: "cada mes",
}


@dataclass(frozen=True)
class TaskBriefingFacts:
    pending_today: int
    overdue: int
    overdue_titles: tuple[str, ...]
    pending_titles: tuple[str, ...]
    next_title: str | None
    next_due: str | None
    next_repeat: str | None

    @property
    def empty(self) -> bool:
        return self.pending_today == 0 and self.overdue == 0


def collect_briefing_facts(
    snapshot: TasksSnapshot,
    *,
    upcoming: tuple[TaskSnapshot, ...] = (),
    max_titles: int = _MAX_TITLES,
) -> TaskBriefingFacts:
    overdue = tuple(
        item for item in snapshot.tasks if item.status == TASK_STATUS_OVERDUE
    )
    pending = tuple(
        item for item in snapshot.tasks if item.status == TASK_STATUS_PENDING
    )
    nxt = overdue[0] if overdue else pending[0] if pending else upcoming[0] if upcoming else None
    return TaskBriefingFacts(
        pending_today=int(snapshot.pending_today_count),
        overdue=int(snapshot.overdue_count),
        overdue_titles=tuple(item.title for item in overdue[:max_titles]),
        pending_titles=tuple(item.title for item in pending[:max_titles]),
        next_title=None if nxt is None else nxt.title,
        next_due=None if nxt is None else nxt.due_date,
        next_repeat=None if nxt is None else _repeat_hint(nxt.repeat),
    )


def facts_from_provider(
    provider: LocalTaskProvider,
    *,
    today: date | None = None,
) -> TaskBriefingFacts:
    when = today if today is not None else date.today()
    provider.reload_if_changed()
    records = provider.records()
    board = provider.board(when)
    snapshot = TasksSnapshot(
        today=when.isoformat(),
        tasks=board,
        overdue_count=overdue_count(records, when),
        pending_today_count=pending_today_count(records, when),
    )
    return collect_briefing_facts(
        snapshot,
        upcoming=upcoming_tasks(records, when),
    )


def build_briefing_prompt(facts: TaskBriefingFacts) -> str:
    lines = [
        f"tareas pendientes hoy: {facts.pending_today}",
        f"tareas vencidas: {facts.overdue}",
    ]
    if facts.overdue_titles:
        lines.append("vencidas relevantes: " + ", ".join(facts.overdue_titles))
    if facts.pending_titles:
        lines.append("pendientes relevantes: " + ", ".join(facts.pending_titles))
    if facts.next_title:
        nxt = facts.next_title
        if facts.next_due:
            nxt = f"{nxt} ({facts.next_due})"
        lines.append(f"próxima tarea: {nxt}")
    if facts.next_repeat:
        lines.append(f"recurrencia: {facts.next_repeat}")
    context = "\n".join(f"- {line}" for line in lines)
    return (
        f"Contexto:\n{context}\n\n"
        "Eres el asistente local del escritorio.\n"
        "Saluda brevemente al usuario y resume su estado de tareas.\n"
        "Sé natural, breve y útil.\n"
        "No inventes tareas ni información.\n"
        "No menciones que eres una IA.\n"
        "No expliques tu proceso.\n"
        "Máximo 1-2 frases."
    )


def fallback_briefing_text(facts: TaskBriefingFacts) -> str:
    if facts.empty:
        return "Todo limpio. No tienes tareas pendientes."
    pending_label = (
        "1 tarea pendiente" if facts.pending_today == 1 else f"{facts.pending_today} tareas pendientes"
    )
    if facts.overdue <= 0:
        return f"Tienes {pending_label} para hoy."
    overdue_label = (
        "1 ya está vencida" if facts.overdue == 1 else f"{facts.overdue} ya están vencidas"
    )
    return f"Tienes {pending_label} para hoy, {overdue_label}."


def _repeat_hint(repeat: str) -> str | None:
    if repeat == TASK_REPEAT_NONE:
        return None
    return _REPEAT_HINTS.get(repeat)


class StartupTaskBriefing:
    """Runs once per Jugoo instance, off the GTK thread."""

    def __init__(
        self,
        *,
        tasks_service=None,
        provider: LocalTaskProvider | None = None,
        notifications=None,
        notify: NotifyFn | None = None,
        resource_monitor: ResourceMonitor | None = None,
        generator: LocalTextGenerator | None = None,
        config: WatcherConfig | None = None,
        which: WhichFn | None = None,
    ) -> None:
        self._tasks_service = tasks_service
        self._provider = provider
        self._notifications = notifications
        self._notify_fn = notify
        self._config = config if config is not None else WatcherConfig.from_shell()
        self._resources = resource_monitor if resource_monitor is not None else ResourceMonitor()
        self._generator = (
            generator if generator is not None else LocalTextGenerator(self._config)
        )
        self._which = which or shutil.which
        self._started = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self._started

    def schedule(self) -> bool:
        """Arm once and run in a worker. Safe as a GLib idle/timeout callback."""
        if not self._arm():
            return False
        worker = threading.Thread(
            target=self._execute,
            name="task-startup-briefing",
            daemon=True,
        )
        self._thread = worker
        worker.start()
        return False

    def run(self) -> str:
        if not self._arm():
            return "already"
        return self._execute()

    def _execute(self) -> str:
        print("Task startup briefing: started")
        if self._closed or not self._config.briefing_enabled:
            print("Task startup briefing: skipped")
            return "skipped"
        facts = self._facts()
        print(
            "Task startup briefing: "
            f"{facts.pending_today} pending, {facts.overdue} overdue"
        )
        fallback = fallback_briefing_text(facts)
        use_ai, reason = self._ai_is_viable()
        if not use_ai:
            print(f"Task startup briefing: AI unavailable ({reason})")
            print("Task startup briefing: using fallback")
            self._deliver(fallback)
            return "fallback"
        print("Task startup briefing: resource check passed")
        print("Task startup briefing: launching llama-cli")
        prompt = build_briefing_prompt(facts)
        body, source = generate_briefing_text(
            prompt,
            config=self._config,
            generator=self._generator,
            use_ai=True,
            fallback=fallback,
        )
        if source == "ai":
            print("Task startup briefing: llama-cli exited, output accepted")
        else:
            reason = getattr(self._generator, "last_error", None) or "invalid"
            print(f"Task startup briefing: llama-cli exited, output rejected ({reason})")
            print("Task startup briefing: using fallback")
        self._deliver(body)
        return source

    def close(self) -> None:
        self._closed = True
        self._generator.close()

    def _arm(self) -> bool:
        with self._lock:
            if self._started:
                return False
            self._started = True
            return True

    def _facts(self) -> TaskBriefingFacts:
        service = self._tasks_service
        if service is not None:
            snapshot = service.snapshot
            upcoming = service.upcoming() if hasattr(service, "upcoming") else ()
            return collect_briefing_facts(snapshot, upcoming=upcoming)
        if self._provider is None:
            self._provider = LocalTaskProvider()
        return facts_from_provider(self._provider)

    def _ai_is_viable(self) -> tuple[bool, str]:
        if not self._config.ai_enabled:
            return False, "ai_disabled"
        if self._which(self._config.ai_binary) is None:
            return False, "binary_missing"
        viability = self._resources.viability(self._config)
        if not viability.viable:
            return False, viability.reason
        return True, "ok"

    def _deliver(self, body: str) -> None:
        if self._closed:
            return
        if self._notify_fn is not None:
            self._emit_notification(body)
            return
        if GLib is not None:
            GLib.idle_add(self._emit_notification, body)
            return
        self._emit_notification(body)

    def _emit_notification(self, body: str) -> bool:
        if self._closed:
            return False
        if self._notify_fn is not None:
            self._notify_fn(body)
        elif self._notifications is not None:
            poster = getattr(self._notifications, "post", None)
            if callable(poster):
                poster(
                    app_name="Jugoo",
                    summary="Jugoo Tasks",
                    body=body,
                    app_icon="com.jugoo.Shell",
                    urgency=1,
                    expire_timeout_ms=self._config.notification_timeout_ms,
                )
        print("Task startup briefing: notification sent")
        return False
