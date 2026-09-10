"""One-shot Task startup briefing. Separate from the resident Task Watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import re
import shutil
import threading
import traceback
import uuid
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
from ...runtime_paths import briefing_path
from .briefing_memory import (
    BriefingMemory,
    describe_briefing_changes,
    load_briefing_memory,
    save_briefing_memory,
)
from .logic import format_day_label, overdue_count, pending_today_count, upcoming_tasks
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
_MAX_RELEVANT = 6
_MAX_NOTES_CHARS = 120
_REPEAT_HINTS = {
    TASK_REPEAT_DAILY: "diaria",
    TASK_REPEAT_MONTHLY: "cada mes",
}
_DIAG_LOG_PATH = Path("/tmp/jugoo-briefing-diag.log")
_thread_run_id = threading.local()


@dataclass(frozen=True)
class BriefingTaskItem:
    """One task line prepared for the Llama context (no ids)."""

    title: str
    status_label: str
    notes: str = ""


@dataclass(frozen=True)
class TaskBriefingFacts:
    pending_today: int
    overdue: int
    upcoming: int
    total_open: int
    overdue_titles: tuple[str, ...]
    pending_titles: tuple[str, ...]
    relevant_tasks: tuple[BriefingTaskItem, ...]
    most_urgent: BriefingTaskItem | None
    next_title: str | None
    next_due: str | None
    next_repeat: str | None
    open_ids: tuple[str, ...] = ()
    previous_message: str | None = None
    changes_summary: str | None = None

    @property
    def empty(self) -> bool:
        return self.total_open == 0


def collect_briefing_facts(
    snapshot: TasksSnapshot,
    *,
    upcoming: tuple[TaskSnapshot, ...] = (),
    today: date | None = None,
    max_titles: int = _MAX_RELEVANT,
    memory: BriefingMemory | None = None,
) -> TaskBriefingFacts:
    when = today if today is not None else date.today()
    overdue = tuple(
        item for item in snapshot.tasks if item.status == TASK_STATUS_OVERDUE
    )
    pending = tuple(
        item for item in snapshot.tasks if item.status == TASK_STATUS_PENDING
    )
    upcoming_pending = tuple(
        item for item in upcoming if item.status == TASK_STATUS_PENDING
    )

    relevant: list[BriefingTaskItem] = []
    seen_titles: set[str] = set()
    for item in (*overdue, *pending, *upcoming_pending):
        key = item.title.casefold()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        relevant.append(_task_item(item, when))
        if len(relevant) >= max_titles:
            break

    urgent_source = overdue[0] if overdue else pending[0] if pending else (
        upcoming_pending[0] if upcoming_pending else None
    )
    most_urgent = None if urgent_source is None else _task_item(urgent_source, when)
    nxt = urgent_source

    open_ids = tuple(
        dict.fromkeys(
            item.id for item in (*overdue, *pending, *upcoming_pending) if item.id
        )
    )
    upcoming_count = len(upcoming_pending)
    total_open = len(open_ids)
    changes = describe_briefing_changes(
        memory,
        open_ids=open_ids,
        overdue=int(snapshot.overdue_count),
        pending_today=int(snapshot.pending_today_count),
        upcoming=upcoming_count,
    )
    raw_previous = None if memory is None else memory.last_message.strip() or None
    previous = _safe_previous_message(raw_previous)

    return TaskBriefingFacts(
        pending_today=int(snapshot.pending_today_count),
        overdue=int(snapshot.overdue_count),
        upcoming=upcoming_count,
        total_open=total_open,
        overdue_titles=tuple(item.title for item in overdue[:max_titles]),
        pending_titles=tuple(item.title for item in pending[:max_titles]),
        relevant_tasks=tuple(relevant),
        most_urgent=most_urgent,
        next_title=None if nxt is None else nxt.title,
        next_due=None if nxt is None else nxt.due_date,
        next_repeat=None if nxt is None else _repeat_hint(nxt.repeat),
        open_ids=open_ids,
        previous_message=previous,
        changes_summary=changes,
    )


def facts_from_provider(
    provider: LocalTaskProvider,
    *,
    today: date | None = None,
    memory: BriefingMemory | None = None,
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
        today=when,
        memory=memory,
    )


def build_briefing_prompt(facts: TaskBriefingFacts) -> str:
    relevant_lines = []
    for item in facts.relevant_tasks:
        line = f'- {item.title} — {item.status_label}'
        if item.notes:
            line += f"\n  Descripción: {item.notes}"
        relevant_lines.append(line)
    relevant_block = "\n".join(relevant_lines) if relevant_lines else "- (ninguna)"

    if facts.most_urgent is None:
        urgent_block = "- (ninguna)"
    else:
        urgent = facts.most_urgent
        urgent_block = f"- {urgent.title} — {urgent.status_label}"
        if urgent.notes:
            urgent_block += f"\n  Descripción: {urgent.notes}"

    changes = facts.changes_summary or (
        "CAMBIOS DESDE EL ÚLTIMO BRIEFING:\n- Primer briefing (sin memoria previa)."
    )

    previous_block = "(no hay mensaje anterior)"
    if facts.previous_message:
        previous_block = f'"{facts.previous_message}"'

    return (
        "ESTADO ACTUAL:\n\n"
        f"Tareas vencidas: {facts.overdue}\n"
        f"Tareas para hoy: {facts.pending_today}\n"
        f"Tareas próximas: {facts.upcoming}\n"
        f"Tareas pendientes en total: {facts.total_open}\n\n"
        "TAREAS RELEVANTES:\n"
        f"{relevant_block}\n\n"
        "TAREA MÁS URGENTE:\n"
        f"{urgent_block}\n\n"
        f"{changes}\n\n"
        "MENSAJE ANTERIOR DEL ASISTENTE "
        "(solo continuidad; no implica qué hizo el usuario después):\n"
        f"{previous_block}\n\n"
        "Escribe ahora el mensaje breve que verá el usuario."
    )


def _assistant_meta(facts: TaskBriefingFacts) -> str:
    """Discrete footer for the assistant card (not a task list)."""
    if facts.empty:
        return ""
    parts: list[str] = []
    if facts.overdue == 1:
        parts.append("1 vencida")
    elif facts.overdue > 1:
        parts.append(f"{facts.overdue} vencidas")
    if facts.pending_today == 1:
        parts.append("1 pendiente")
    elif facts.pending_today > 1:
        parts.append(f"{facts.pending_today} pendientes")
    return " · ".join(parts)


def fallback_briefing_text(facts: TaskBriefingFacts) -> str:
    caller = "?"
    for frame in reversed(traceback.extract_stack(limit=12)[:-1]):
        if frame.name != "fallback_briefing_text":
            caller = f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            break
    _briefing_diag("FALLBACK_TEXT_BUILT")
    _briefing_diag(f"FALLBACK_CALLER={caller}")
    if facts.empty:
        text = "Todo limpio. No tienes tareas pendientes."
    else:
        pending_label = (
            "1 tarea pendiente"
            if facts.pending_today == 1
            else f"{facts.pending_today} tareas pendientes"
        )
        if facts.overdue <= 0:
            text = f"Tienes {pending_label} para hoy."
        else:
            overdue_label = (
                "1 ya está vencida"
                if facts.overdue == 1
                else f"{facts.overdue} ya están vencidas"
            )
            text = f"Tienes {pending_label} para hoy, {overdue_label}."
    _briefing_diag(f"FALLBACK_TEXT={text!r}")
    return text


def _task_item(snapshot: TaskSnapshot, today: date) -> BriefingTaskItem:
    return BriefingTaskItem(
        title=snapshot.title.strip(),
        status_label=_status_label(snapshot, today),
        notes=_clip_notes(snapshot.notes),
    )


def _status_label(snapshot: TaskSnapshot, today: date) -> str:
    if snapshot.status == TASK_STATUS_OVERDUE:
        return "vencida"
    if snapshot.repeat == TASK_REPEAT_DAILY:
        # Avoid the literal "cada día" — Llama tended to turn it into an order.
        return "hoy · diaria"
    occurrence = _parse_date(snapshot.occurrence_date) or _parse_date(snapshot.due_date)
    if occurrence is None:
        return "pendiente"
    if occurrence == today:
        return "hoy"
    if occurrence == today + timedelta(days=1):
        return "mañana"
    return format_day_label(occurrence)


def _clip_notes(notes: str) -> str:
    cleaned = " ".join((notes or "").split())
    if len(cleaned) <= _MAX_NOTES_CHARS:
        return cleaned
    return cleaned[: _MAX_NOTES_CHARS - 1].rstrip() + "…"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _repeat_hint(repeat: str) -> str | None:
    if repeat == TASK_REPEAT_NONE:
        return None
    return _REPEAT_HINTS.get(repeat)


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


# Phrases that invent user behavior or give unverified orders.
_INVENTED_USER_ACTION_PATTERNS = (
    r"\bno has(?:\s+\w+){0,3}\s+"
    r"(?:comenzado|empezado|trabajado|hecho|terminado|arreglado|"
    r"dejado|avanzado|cumplido|podido)\b",
    r"\bparece que no has\b",
    r"\btodav[ií]a no has\b",
    r"\bdesde mi [uú]ltimo mensaje\b",
    r"\bcomo (?:todav[ií]a )?no (?:has|hiciste|empezaste)\b",
    r"\brecuerda(?:\s+tambi[eé]n)?(?:\s+hacer(?:lo)?|\s+que)\b",
    r"\bno te olvides\b",
    r"\bno olvides\b",
    r"\bacu[eé]rdate(?:\s+tambi[eé]n)?(?:\s+de)?\b",
    r"\bdeber[ií]as\b",
    r"\bdebiste\b",
    r"\bnecesitas (?:hacer|arreglar|empezar|trabajar)\b",
    r"\bhazlo cada d[ií]a\b",
    r"\bhacer(?:lo)? cada d[ií]a\b",
)


def _invented_user_action_hit(normalized: str) -> str | None:
    for pattern in _INVENTED_USER_ACTION_PATTERNS:
        if re.search(pattern, normalized):
            return pattern
    return None


def _safe_previous_message(previous: str | None) -> str | None:
    """Drop prior assistant text that would poison continuity with false claims."""
    if not previous:
        return None
    if _invented_user_action_hit(_normalized_text(previous)) is None:
        return previous
    return (
        "Había un briefing previo sobre las mismas tareas abiertas "
        "(el texto anterior no era fiable)."
    )


def _title_is_echoed(text: str, title: str) -> bool:
    """True only when the model dumped the bare title (optionally with junk).

    Starting with the title in a natural sentence is allowed — that is often
    the intended reaction to the task content.
    """
    normalized_text = _normalized_text(text)
    normalized_title = _normalized_text(title)
    if not normalized_title:
        return False
    if normalized_text == normalized_title:
        return True
    # Title + raw ISO date leftover from older echo bugs.
    if re.fullmatch(
        re.escape(normalized_title) + r"(?:\s+\d{4}-\d{2}-\d{2})?",
        normalized_text,
    ):
        return True
    return False


def validate_briefing_output(text: str, facts: TaskBriefingFacts) -> str | None:
    """Reject locally obvious hallucinations/echoes before notifying the user."""
    accepted, _reason = validate_briefing_output_with_reason(text, facts)
    return accepted


def validate_briefing_output_with_reason(
    text: str,
    facts: TaskBriefingFacts,
) -> tuple[str | None, str]:
    """Like ``validate_briefing_output`` but also returns a reject reason."""
    if not text or not text.strip():
        return None, "empty"

    normalized = _normalized_text(text)

    for title in (*facts.overdue_titles, *facts.pending_titles):
        if _title_is_echoed(text, title):
            return None, f"bare_title_echo:{title[:40]}"

    if facts.next_title and _title_is_echoed(text, facts.next_title):
        return None, f"bare_title_echo:{facts.next_title[:40]}"

    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", text):
        return None, "iso_date"

    if facts.empty:
        positive_claims = (
            "tienes una tarea pendiente",
            "tienes tareas pendientes",
            "hay una tarea pendiente",
            "hay tareas pendientes",
            "tienes una tarea vencida",
            "tienes tareas vencidas",
            "hay una tarea vencida",
            "hay tareas vencidas",
        )
        if any(phrase in normalized for phrase in positive_claims):
            return None, "contradicts_empty_board"

    if facts.pending_today > 0:
        no_pending_claims = (
            "no tienes tareas pendientes",
            "no tienes ninguna tarea pendiente",
            "no hay tareas pendientes",
        )
        if any(phrase in normalized for phrase in no_pending_claims):
            return None, "contradicts_pending"

    invent_hit = _invented_user_action_hit(normalized)
    if invent_hit is not None:
        return None, f"invented_user_action:{invent_hit}"

    if facts.overdue > 0:
        no_overdue_claims = (
            "no tienes tareas vencidas",
            "no tienes ninguna tarea vencida",
            "no hay tareas vencidas",
        )
        if any(phrase in normalized for phrase in no_overdue_claims):
            return None, "contradicts_overdue"

    if facts.previous_message and _normalized_text(facts.previous_message) == normalized:
        return None, "exact_repeat_of_previous"

    return text.strip(), "accepted"


def _run_id() -> str:
    return getattr(_thread_run_id, "value", "no-id")


def _briefing_diag(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    line = f"[Briefing:{_run_id()} {stamp}] {message}"
    print(line, flush=True)
    try:
        with _DIAG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _clip_diag(value: str | None, *, limit: int = 4000) -> str:
    if value is None:
        return "(none)"
    text = value if value.strip() else "(empty)"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _safe_command_preview(argv: list[str] | None) -> str:
    """Show argv without dumping the full ChatML prompt payload."""
    if not argv:
        return "(none)"
    preview: list[str] = []
    skip_next = False
    for index, item in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if item == "-p":
            preview.append("-p")
            preview.append(
                f"<prompt:{len(argv[index + 1]) if index + 1 < len(argv) else 0} chars>"
            )
            skip_next = True
            continue
        preview.append(item)
    return " ".join(preview)


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
        memory_path: Path | None = None,
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
        self._memory_path = memory_path if memory_path is not None else briefing_path()
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
        _thread_run_id.value = uuid.uuid4().hex[:8]
        _briefing_diag("START")
        model = self._config.model_file()
        _briefing_diag(
            "runtime: "
            f"euid={os.geteuid()} home={os.environ.get('HOME', '')!r} "
            f"pid={os.getpid()} thread={threading.current_thread().name!r}"
        )
        _briefing_diag(
            "ai_config: "
            f"enabled={self._config.ai_enabled} binary={self._config.ai_binary!r} "
            f"model={str(model)!r} model_exists={model.is_file()} "
            f"which_binary={self._which(self._config.ai_binary)!r}"
        )
        if self._closed or not self._config.briefing_enabled:
            _briefing_diag("skipped (disabled or closed)")
            _briefing_diag("fallback_used: YES")
            _briefing_diag("fallback_reason: skipped")
            _briefing_diag("END")
            return "skipped"
        memory = load_briefing_memory(self._memory_path)
        facts = self._facts(memory=memory)
        _briefing_diag(
            "facts: "
            f"empty={facts.empty} pending_today={facts.pending_today} "
            f"overdue={facts.overdue} upcoming={facts.upcoming} "
            f"total_open={facts.total_open} "
            f"titles={list(facts.pending_titles) + list(facts.overdue_titles)!r} "
            f"urgent={None if facts.most_urgent is None else facts.most_urgent.title!r}"
        )
        previous = facts.previous_message or "(none)"
        _briefing_diag(f"previous: {_clip_diag(previous, limit=240)}")
        fallback = fallback_briefing_text(facts)
        _briefing_diag(f"fallback_candidate: {fallback!r}")
        use_ai, reason = self._ai_is_viable()
        _briefing_diag(f"ai_viability={'YES' if use_ai else 'NO'}")
        _briefing_diag(f"viability_reason={reason}")
        if not use_ai:
            _briefing_diag("llama_start: SKIPPED")
            _briefing_diag("llama_command: (not executed)")
            _briefing_diag("llama_returncode: (n/a)")
            _briefing_diag("llama_stdout: (n/a)")
            _briefing_diag("llama_stderr: (n/a)")
            _briefing_diag("llama_parsed: (n/a)")
            _briefing_diag("validation: SKIPPED")
            _briefing_diag(f"rejection_reason: ai_unavailable:{reason}")
            _briefing_diag("selected_source=fallback")
            _briefing_diag("fallback_used: YES")
            _briefing_diag(f"fallback_reason: ai_unavailable:{reason}")
            _briefing_diag(f"final_body: {fallback!r}")
            _briefing_diag("END")
            self._deliver(
                fallback,
                meta=_assistant_meta(facts),
                source="fallback",
            )
            self._persist(fallback, facts)
            return "fallback"

        prompt = build_briefing_prompt(facts)
        _briefing_diag(f"prompt_length: {len(prompt)}")
        _briefing_diag("prompt_preview_start:")
        print(prompt[:1200], flush=True)
        if len(prompt) > 1200:
            _briefing_diag(f"prompt_preview_truncated: +{len(prompt) - 1200} chars")
        for marker in (
            "ESTADO ACTUAL:",
            "TAREAS RELEVANTES:",
            "CAMBIOS DESDE EL ÚLTIMO BRIEFING:",
            "MENSAJE ANTERIOR DEL ASISTENTE:",
        ):
            _briefing_diag(f"prompt_has[{marker}]={marker in prompt}")

        try:
            body = fallback
            source = "fallback"
            last_reject = ""
            for attempt in (1, 2):
                _briefing_diag(f"llama_start attempt={attempt}")
                body, source = generate_briefing_text(
                    prompt,
                    config=self._config,
                    generator=self._generator,
                    use_ai=True,
                    fallback=fallback,
                )
                _briefing_diag(f"llama_end attempt={attempt}")

                gen = self._generator
                _briefing_diag(
                    f"llama_command: {_safe_command_preview(getattr(gen, 'last_argv', None))}"
                )
                _briefing_diag(
                    f"llama_returncode: {getattr(gen, 'last_returncode', None)}"
                )
                _briefing_diag("llama_stdout:")
                print(_clip_diag(getattr(gen, "last_stdout", None)), flush=True)
                _briefing_diag("llama_stderr:")
                print(
                    _clip_diag(getattr(gen, "last_stderr", None), limit=1500),
                    flush=True,
                )

                if source != "ai":
                    ai_reason = getattr(gen, "last_error", None) or "invalid"
                    _briefing_diag(f"llama_parsed: (none) parser_error={ai_reason!r}")
                    _briefing_diag(f"validation=SKIPPED reason=parser:{ai_reason}")
                    last_reject = f"parser:{ai_reason}"
                    body = fallback
                    source = "fallback"
                    break

                _briefing_diag(f"llama_parsed: {body!r}")
                validated_body, reject_reason = validate_briefing_output_with_reason(
                    body, facts
                )
                if validated_body is None:
                    _briefing_diag(
                        f"validation=REJECTED attempt={attempt} reason={reject_reason}"
                    )
                    last_reject = f"semantic_reject:{reject_reason}"
                    if attempt == 1:
                        _briefing_diag("retry_after_semantic_reject=YES")
                        continue
                    _briefing_diag("selected_source=fallback")
                    _briefing_diag("fallback_used: YES")
                    _briefing_diag(f"fallback_reason: {last_reject}")
                    body = fallback
                    source = "fallback"
                    break

                body = validated_body
                _briefing_diag("validation=ACCEPTED")
                _briefing_diag("selected_source=ai")
                _briefing_diag("fallback_used: NO")
                _briefing_diag("fallback_reason: (none)")
                source = "ai"
                break
        except Exception as error:
            _briefing_diag("selected_source=fallback")
            _briefing_diag("fallback_used: YES")
            _briefing_diag(f"fallback_reason: exception:{error!r}")
            print(f"Task startup briefing: generation failed: {error}", flush=True)
            body = fallback
            source = "fallback"

        _briefing_diag(f"final_body: {body!r}")
        _briefing_diag(f"message_source: {source}")
        _briefing_diag("END")
        self._deliver(body, meta=_assistant_meta(facts), source=source)
        self._persist(body, facts)
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

    def _facts(self, *, memory: BriefingMemory | None = None) -> TaskBriefingFacts:
        service = self._tasks_service
        if service is not None:
            snapshot = service.snapshot
            upcoming = service.upcoming() if hasattr(service, "upcoming") else ()
            return collect_briefing_facts(snapshot, upcoming=upcoming, memory=memory)
        if self._provider is None:
            self._provider = LocalTaskProvider()
        return facts_from_provider(self._provider, memory=memory)

    def _persist(self, body: str, facts: TaskBriefingFacts) -> None:
        save_briefing_memory(
            body,
            open_ids=facts.open_ids,
            overdue=facts.overdue,
            pending_today=facts.pending_today,
            upcoming=facts.upcoming,
            path=self._memory_path,
        )

    def _ai_is_viable(self) -> tuple[bool, str]:
        if not self._config.ai_enabled:
            return False, "ai_disabled"
        if self._which(self._config.ai_binary) is None:
            return False, "binary_missing"
        viability = self._resources.viability(self._config)
        if viability.viable:
            return True, "ok"

        details = []
        if viability.available_vram_bytes is not None:
            details.append(
                f"VRAM libre {viability.available_vram_bytes // (1024 * 1024)} MiB"
            )
        if viability.estimated_vram_bytes is not None:
            details.append(
                f"estimada {viability.estimated_vram_bytes // (1024 * 1024)} MiB"
            )
        suffix = f" ({', '.join(details)})" if details else ""

        # Startup briefing: the VRAM gate uses a conservative full-model estimate
        # on an 8 GiB card that often sits a few hundred MiB below the estimate
        # while llama-cli still runs fine. Soft-pass any pure `vram` reject and
        # let the real llama call (timeout / empty / error) decide fallback.
        if viability.reason == "vram":
            free = viability.available_vram_bytes
            est = viability.estimated_vram_bytes
            _briefing_diag(
                "ai_viable: soft-pass vram attempt "
                f"(est={None if est is None else est // (1024 * 1024)} MiB, "
                f"free={None if free is None else free // (1024 * 1024)} MiB)"
            )
            return True, "ok_vram_soft_attempt"

        return False, f"{viability.reason}{suffix}"

    def _deliver(self, body: str, *, meta: str = "", source: str = "fallback") -> None:
        if self._closed:
            return
        run_id = _run_id()

        def _emit_with_id() -> bool:
            _thread_run_id.value = run_id
            return self._emit_notification(body, meta=meta, source=source)

        if self._notify_fn is not None:
            _emit_with_id()
            return
        if GLib is not None:
            GLib.idle_add(_emit_with_id)
            return
        _emit_with_id()

    def _emit_notification(
        self,
        body: str,
        *,
        meta: str = "",
        source: str = "fallback",
    ) -> bool:
        if self._closed:
            return False
        _briefing_diag(f"notification EMIT body={body!r} source={source!r}")
        if self._notify_fn is not None:
            self._notify_fn(body)
            _briefing_diag("notification ROUTE=notify_fn")
        elif self._notifications is not None:
            poster = getattr(self._notifications, "post_assistant", None)
            if callable(poster):
                try:
                    snapshot = poster(
                        body=body,
                        app_name="Jugoo",
                        app_icon="com.jugoo.Shell",
                        meta=meta,
                        source=source,
                        urgency=1,
                        expire_timeout_ms=self._config.notification_timeout_ms,
                    )
                    snap_id = getattr(snapshot, "id", None)
                    snap_source = getattr(snapshot, "source", None)
                    _briefing_diag(
                        f"post_assistant source={snap_source!r} notification_id={snap_id}"
                    )
                    _briefing_diag(
                        "notification ROUTE=NotificationService.post_assistant"
                    )
                except Exception as error:
                    _briefing_diag(f"notification ROUTE=failed:{error!r}")
                    print(
                        f"Task startup briefing: assistant notify failed: {error}",
                        flush=True,
                    )
            else:
                _briefing_diag("notification ROUTE=none (no post_assistant)")
        else:
            _briefing_diag("notification ROUTE=none (no target)")
        _briefing_diag("notification SENT")
        return False
