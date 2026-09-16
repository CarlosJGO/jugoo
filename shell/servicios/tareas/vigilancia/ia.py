"""Opportunistic local text generation via on-demand llama-cli."""
from __future__ import annotations
from pathlib import Path
import os
import re
import shutil
import signal
import subprocess
from typing import Callable
from .config import WatcherConfig
from .estado import ReminderState
from .politica import HIGH_MENTION_THRESHOLD, ActivitySnapshot, ReminderDecision, due_label

FALLBACK_PHRASES = (
    "Ey, acuérdate de {title} 👀",
    "Aja, no te olvides de {title}.",
    "Ojo, {title} sigue pendiente.",
    "Cuando tengas un momento, acuérdate de {title}.",
    "Ey, {title} sigue esperando por ti.",
)
_MAX_OUTPUT_CHARS = 180
_MAX_OUTPUT_WORDS = 28
_BRIEFING_OUTPUT_CHARS = 320
_BRIEFING_OUTPUT_WORDS = 64
_REMINDER_MESSAGE_CHARS = 160
_REMINDER_MESSAGE_WORDS = 22
_REMINDER_BATCH_CHARS = 520
_REMINDER_BATCH_WORDS = 90
_REMINDER_BATCH_LINES = 12
_MAX_REMINDER_MESSAGES = 3
_MESSAGE_DELIMITER = "---"

_REMINDER_SYSTEM_PROMPT = (
    "Eres el asistente personal de Jugoo, un gestor de tareas de escritorio.\n"
    "Hablas como alguien cercano que le manda notificaciones al usuario para "
    "recordarle cosas — no como un sistema, no como un narrador de noticias.\n"
    "Frases cortas, tono relajado y directo, como si hablaras rápido por chat.\n"
    "\n"
    "DATOS QUE RECIBES:\n"
    "- Fecha y hora actual.\n"
    "- Lista de tareas relevantes ahora (título, notas, vencimiento, prioridad, "
    "categoría).\n"
    "- Para cada tarea: cuántas veces ya la mencionaste hoy y cuál fue tu "
    "último mensaje sobre ella (si aplica).\n"
    "- Ventana(s) activa(s) del usuario, si están disponibles.\n"
    "\n"
    "REGLAS DE CONTENIDO (no negociables):\n"
    "1. Habla solo de datos presentes en el contexto. Nunca inventes cómo fue "
    "el día del usuario, su ánimo, o acciones que no están en los datos.\n"
    "2. Nunca digas que una tarea se empezó, terminó o se ignoró sin evidencia "
    "explícita en los datos.\n"
    "3. No conviertas tareas recurrentes en órdenes nuevas.\n"
    "4. Nunca digas que eres una IA.\n"
    "\n"
    "REGLAS DE ENFOQUE:\n"
    "5. No enumeres todas las tareas pendientes cada vez. Elige la(s) más "
    "relevante(s) ahora mismo (vencida > por vencer pronto > alta prioridad "
    "> lleva tiempo sin tocarse). Solo lista todas si es explícitamente el "
    "resumen del día.\n"
    "6. Si una tarea tiene \"último_mensaje\" registrado, NO repitas la misma "
    "estructura ni las mismas palabras. Cambia de ángulo: opina sobre el "
    "contenido, haz una pregunta retórica, comenta sobre las notas, o si de "
    "plano no hay nada nuevo que aportar, omite esa tarea.\n"
    f"7. Si una tarea lleva {HIGH_MENTION_THRESHOLD} o más menciones hoy, cambia de "
    "táctica obligatoriamente o no la menciones en este turno.\n"
    "8. Puedes opinar con criterio sobre una tarea (si suena difícil, urgente, "
    "rara, tediosa, mal planeada) usando su título y notas — no te limites "
    "a repetirla tal cual.\n"
    "\n"
    "FORMATO DE SALIDA:\n"
    "- Puedes generar hasta 3 mensajes independientes si hay más de una cosa "
    "que vale la pena decir; genera solo 1 si con eso basta. Nunca fuerces "
    "3 solo por llenar el cupo.\n"
    "- Separa cada mensaje con una línea que contenga únicamente: ---\n"
    "- Cada mensaje: 1 frase, máximo ~20 palabras. Nada de listas ni viñetas "
    "dentro de un mensaje.\n"
    "- No agregues comillas, explicaciones, ni texto fuera de los mensajes.\n"
    "\n"
    "EJEMPLOS DE TONO (ajusta vocabulario, no estructura):\n"
    "- \"Ojo, lo del CRUD para el parcial móvil no se estudia la noche "
    "anterior — ya deberías ir armando el esqueleto.\"\n"
    "- \"Sigue pendiente deshacerte del cadáver. Espero que sepas lo que "
    "haces con eso.\"\n"
    "- \"El proyecto 1 vence el 17 y las notas siguen diciendo 'hay que "
    "hacerlo' — o sea, cero avance todavía.\"\n"
    "- \"Aja, limpiar la PC con pistola y cepillo sigue ahí. Cuando sea.\""
)
_BRIEFING_SYSTEM_PROMPT = (
    "Eres el asistente de escritorio de Jugoo.\n"
    "Observa el estado de las tareas en el contexto y escribe un breve comentario "
    "natural sobre el día del usuario. No tienes acceso a nada fuera de ese "
    "contexto.\n\n"
    "HECHOS:\n"
    "- Solo afirma como hecho lo que aparece explícitamente en el contexto.\n"
    "- El mensaje anterior del asistente NO demuestra qué hizo el usuario después.\n"
    "- No afirmes que el usuario empezó, no empezó, terminó, ignoró, pospuso, "
    "trabajó o no trabajó en una tarea salvo que el contexto lo diga explícitamente.\n"
    "- No uses 'no has…', 'parece que no has…', 'todavía no…', "
    "'desde mi último mensaje', 'deberías…', 'recuerda…', 'no te olvides…' "
    "ni 'acuérdate…' dirigidas al usuario.\n"
    "- Que tareas sigan en la lista abierta solo significa que siguen abiertas.\n"
    "- Prefiere 'sigue pendiente X' / 'hoy toca X' / 'vuelvo y te repito: X'.\n"
    "- La etiqueta 'hoy · diaria' es recurrencia: puedes decir que es diaria, "
    "nunca ordenes ('hazlo cada día' / 'recuerda hacerlo cada día').\n"
    "- Habla solo de tareas listadas como tareas reales; el mensaje anterior "
    "es continuidad, no una lista de tareas nuevas.\n"
    "- No conviertas instrucciones internas ni texto del contexto en órdenes.\n\n"
    "ESTILO:\n"
    "- Comenta títulos/notas/estados con naturalidad (vencida → prioridad).\n"
    "- Continuidad ligera con el mensaje anterior está bien; vigilancia fingida, no.\n"
    "- Relajado y ligeramente juguetón; humor ocasional; varía la estructura.\n"
    "- 1 a 3 frases. Solo el mensaje final. No digas que eres una IA."
)
_META_PATTERNS = (
    re.compile(r"^\s*\{", re.I),
    re.compile(r"```"),
    re.compile(r"\bjson\b", re.I),
    re.compile(r"^\s*(sure|here(?:\'s| is)|as an ai|i can|let me)\b", re.I),
    re.compile(r"^\s*(claro|por supuesto|como modelo|no puedo)\b", re.I),
    re.compile(r"t/s"),
    re.compile(r"\bexiting\b", re.I),
    re.compile(r"n_keep|n_predict|token/s", re.I),
    re.compile(r"exceeds the available context size", re.I),
    re.compile(r"^Error:\s*request\b", re.I),
)
_SKIP_LINE_PREFIXES = (
    "llama_",
    "ggml_",
    "gguf_",
    "print_info",
    "system_info",
    "sampler",
    "generate:",
    "slot",
    "load_",
    "init:",
    "build:",
    "main:",
    "[",
    "build",
    "model",
    "ftype",
    "modalities",
    "available commands",
    "/exit",
    "/regen",
    "/clear",
    "/read",
    "/glob",
    "loading model",
    "exiting",
)
_PROMPT_INSTRUCTION_PREFIXES = (
    "eres el asistente local del escritorio.",
    "saluda brevemente al usuario y resume su estado de tareas.",
    "escribe una notificación matutina breve y natural sobre el estado de sus tareas.",
    "resume primero las tareas vencidas y las de hoy; menciona una tarea por su título solo si aporta contexto.",
    "si no hay tareas para hoy, dilo claramente y menciona la próxima solo si existe.",
    "puedes saludar, pero no uses una charla genérica como 'hola, ¿cómo te va?'.",
    "no copies las etiquetas de los datos internos ni hagas una lista.",
    "no muestres fechas, fechas iso, paréntesis ni el formato 'título (fecha)'.",
    "sé natural, breve y útil.",
    "no inventes tareas ni información.",
    "no menciones que eres una ia.",
    "no expliques tu proceso.",
    "máximo 1-2 frases.",
    "responde con una o dos frases breves en español.",
    "tono natural y cercano.",
    "sin explicaciones.",
    "datos internos de tareas:",
    "estado de tareas",
    "datos de tareas",
    "tareas vencidas",
    "tareas para hoy",
    "resumen",
    "próxima tarea",
    "trabajo",
    "ejemplo",
    "respuesta",
)
_CONTEXT_FIELD = re.compile(
    r"^-\s*(?:tareas pendientes hoy|tareas vencidas|vencidas relevantes|"
    r"pendientes relevantes|próxima tarea|próxima tarea relevante|recurrencia):\s*",
    re.I,
)
_CONTEXT_FIELD_VALUES = (
    "cada día",
    "cada mes",
)
_LEADING_TITLE_ECHO = re.compile(
    r"^(?!(?:¡)?(?:hola|hey|buenos|buenas)\b)([^.!?]{1,60}?)\s+"
    r"(?=(?:¡)?(?:hola|hey|buenos|buenas)\b)",
    re.I,
)
_TRUNCATED_MARKER = "... (truncated)"
LlamaRunner = Callable[..., subprocess.Popen]

def fallback_phrase(title: str, *, seed: str = "") -> str:
    cleaned = _short_title(title)
    index = 0
    if seed:
        index = sum(ord(char) for char in seed) % len(FALLBACK_PHRASES)
    return FALLBACK_PHRASES[index].format(title=cleaned)

def build_reminder_prompt(
    decision: ReminderDecision,
    activity: ActivitySnapshot,
    *,
    now,
    state: ReminderState | None = None,
) -> str:
    when = now if hasattr(now, "strftime") else None
    stamp = when.strftime("%Y-%m-%d %H:%M") if when is not None else str(now)
    candidates = decision.candidates or ((decision.snapshot,) if decision.snapshot else ())
    lines = [
        f"Ahora: {stamp}",
        f"Ventana activa: {activity.label or 'desktop'}",
        "Tareas relevantes:",
    ]
    if not candidates:
        lines.append("- (ninguna)")
    for index, snapshot in enumerate(candidates, start=1):
        memory = state.memory(snapshot.id) if state is not None else None
        due = due_label(snapshot, now) if when is not None else "soon"
        notes = " ".join((snapshot.notes or "").split())
        if len(notes) > 120:
            notes = notes[:119].rstrip() + "…"
        priority = snapshot.priority_id or "sin prioridad"
        category = snapshot.category_id or "sin categoría"
        mentioned = memory.times_mentioned_today if memory is not None else 0
        last_message = memory.last_message if memory is not None else ""
        lines.append(f"{index}. id={snapshot.id}")
        lines.append(f"   título: {snapshot.title}")
        if notes:
            lines.append(f"   notas: {notes}")
        lines.append(f"   vencimiento: {due}")
        lines.append(f"   prioridad: {priority}")
        lines.append(f"   categoría: {category}")
        lines.append(f"   menciones_hoy: {mentioned}")
        if last_message:
            lines.append(f"   último_mensaje: {last_message}")
        elif mentioned >= HIGH_MENTION_THRESHOLD:
            lines.append("   último_mensaje: (sin texto guardado)")
    lines.append(
        "Escribe 1 a 3 mensajes cortos en español según las reglas del sistema."
    )
    return "\n".join(lines)


def _usable_output_lines(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    if _TRUNCATED_MARKER in raw:
        raw = raw.rsplit(_TRUNCATED_MARKER, 1)[-1]
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = _strip_prompt_echo(line)
        if not stripped:
            continue
        lowered = stripped.casefold()
        if any(lowered.startswith(prefix) for prefix in _SKIP_LINE_PREFIXES):
            continue
        if lowered.startswith("exiting") or " t/s" in lowered or "token/s" in lowered:
            continue
        if "<|" in stripped or stripped.startswith(">"):
            continue
        if stripped == _MESSAGE_DELIMITER:
            lines.append(_MESSAGE_DELIMITER)
            continue
        if not any(char.isalpha() for char in stripped):
            continue
        if any(pattern.search(stripped) for pattern in _META_PATTERNS):
            continue
        lines.append(stripped)
    return lines


def _validate_single_reminder_message(text: str) -> str | None:
    cleaned = " ".join(text.split()).strip().strip('"').strip("'")
    if not cleaned:
        return None
    if any(pattern.search(cleaned) for pattern in _META_PATTERNS):
        return None
    if len(cleaned) > _REMINDER_MESSAGE_CHARS:
        return None
    words = cleaned.split()
    if not words or len(words) > _REMINDER_MESSAGE_WORDS:
        return None
    return cleaned


def parse_reminder_messages(raw: str, *, max_messages: int = _MAX_REMINDER_MESSAGES) -> list[str]:
    """Split model output on --- and keep at most ``max_messages`` valid phrases."""
    lines = _usable_output_lines(raw)
    if not lines:
        return []
    chunks: list[list[str]] = [[]]
    for line in lines:
        if line == _MESSAGE_DELIMITER:
            if chunks[-1]:
                chunks.append([])
            continue
        chunks[-1].append(line)
    messages: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        validated = _validate_single_reminder_message(" ".join(chunk))
        if validated:
            messages.append(validated)
        if len(messages) >= max(1, int(max_messages)):
            break
    return messages


def attribute_messages_to_tasks(
    messages: list[str],
    decision: ReminderDecision,
) -> dict[str, str]:
    """Map each task id to the last message that appears to mention it."""
    candidates = list(decision.candidates) if decision.candidates else []
    if decision.snapshot is not None and all(item.id != decision.snapshot.id for item in candidates):
        candidates.insert(0, decision.snapshot)
    if not candidates:
        return {}
    attributed: dict[str, str] = {}
    for message in messages:
        lowered = message.casefold()
        matches = [
            snapshot
            for snapshot in candidates
            if snapshot.title and snapshot.title.casefold() in lowered
        ]
        if not matches and decision.snapshot is not None:
            matches = [decision.snapshot]
        elif not matches:
            matches = [candidates[0]]
        for snapshot in matches:
            attributed[snapshot.id] = message
    if decision.snapshot is not None and decision.snapshot.id not in attributed and messages:
        attributed[decision.snapshot.id] = messages[0]
    return attributed


def _strip_prompt_echo(line: str) -> str:
    stripped = line.strip().strip('"').strip("'")
    if not stripped:
        return ""
    marker_at = stripped.find(_TRUNCATED_MARKER)
    if marker_at >= 0:
        stripped = stripped[marker_at + len(_TRUNCATED_MARKER) :].strip()
    remaining = stripped
    lowered = remaining.casefold()
    changed = True
    while remaining and changed:
        changed = False
        for prefix in _PROMPT_INSTRUCTION_PREFIXES:
            if lowered.startswith(prefix):
                remaining = remaining[len(prefix) :].lstrip(" .")
                lowered = remaining.casefold()
                changed = True
                break
        match = _CONTEXT_FIELD.match(remaining)
        if match:
            remaining = remaining[match.end() :]
            lowered = remaining.casefold()
            for value in _CONTEXT_FIELD_VALUES:
                if lowered.startswith(value):
                    remaining = remaining[len(value) :].lstrip(" .")
                    lowered = remaining.casefold()
                    break
            changed = True
    match = _LEADING_TITLE_ECHO.match(remaining)
    if match and match.group(1).strip():
        remaining = remaining[match.end() :].lstrip()
    return remaining

def validate_ai_output(
    raw: str,
    *,
    max_chars: int = _MAX_OUTPUT_CHARS,
    max_words: int = _MAX_OUTPUT_WORDS,
    max_lines: int = 2,
    join_lines: bool = False,
    keep_newlines: bool = False,
) -> str | None:
    text, _reason = validate_ai_output_with_reason(
        raw,
        max_chars=max_chars,
        max_words=max_words,
        max_lines=max_lines,
        join_lines=join_lines,
        keep_newlines=keep_newlines,
    )
    return text

def validate_ai_output_with_reason(
    raw: str,
    *,
    max_chars: int = _MAX_OUTPUT_CHARS,
    max_words: int = _MAX_OUTPUT_WORDS,
    max_lines: int = 2,
    join_lines: bool = False,
    keep_newlines: bool = False,
) -> tuple[str | None, str]:
    if not raw or not raw.strip():
        return None, "empty_stdout"
    # llama-cli often echoes the prompt and marks truncation; the real reply
    # starts after the last truncated-prompt marker in the whole stream.
    if _TRUNCATED_MARKER in raw:
        raw = raw.rsplit(_TRUNCATED_MARKER, 1)[-1]
    lines = []
    skipped = 0
    for line in raw.splitlines():
        stripped = _strip_prompt_echo(line)
        if not stripped:
            skipped += 1
            continue
        lowered = stripped.casefold()
        if stripped == _MESSAGE_DELIMITER and keep_newlines:
            lines.append(_MESSAGE_DELIMITER)
            continue
        if any(lowered.startswith(prefix) for prefix in _SKIP_LINE_PREFIXES):
            skipped += 1
            continue
        if lowered.startswith("exiting") or " t/s" in lowered or "token/s" in lowered:
            skipped += 1
            continue
        if "<|" in stripped or stripped.startswith(">"):
            skipped += 1
            continue
        if not any(char.isalpha() for char in stripped):
            skipped += 1
            continue
        if any(pattern.search(stripped) for pattern in _META_PATTERNS):
            skipped += 1
            continue
        lines.append(stripped)
    if not lines:
        return None, f"no_usable_lines(skipped={skipped})"
    if len(lines) > max(1, int(max_lines)):
        if join_lines:
            limit = max(1, int(max_lines))
            lines = lines[:limit] if keep_newlines else lines[-limit:]
        else:
            return None, f"too_many_lines:{len(lines)}>{max_lines}"
    if join_lines and keep_newlines:
        text = "\n".join(lines)
    elif join_lines:
        text = " ".join(lines)
    else:
        text = lines[0]
    if len(text) > max_chars:
        return None, f"too_many_chars:{len(text)}>{max_chars}"
    words = text.replace(_MESSAGE_DELIMITER, " ").split()
    if not words:
        return None, "empty_after_join"
    if len(words) > max_words:
        return None, f"too_many_words:{len(words)}>{max_words}"
    if any(pattern.search(text) for pattern in _META_PATTERNS):
        return None, "meta_pattern"
    if "\n" in text and not keep_newlines:
        return None, "contains_newline"
    return text, "accepted"

def _instruct_prompt(user_prompt: str, *, system_prompt: str | None = None) -> str:
    system = system_prompt if system_prompt else _REMINDER_SYSTEM_PROMPT
    return (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        f"{system}"
        "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
        f"{user_prompt}"
        "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    )

def _short_title(title: str, limit: int = 42) -> str:
    cleaned = " ".join(title.split()) or "esa tarea"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"

def _kill_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

class LocalTextGenerator:
    """Launch llama-cli only for a single prompt, then always reap the child."""
    def __init__(
        self,
        config: WatcherConfig,
        *,
        which: Callable[[str], str | None] | None = None,
        popen: LlamaRunner | None = None,
    ) -> None:
        self._config = config
        self._which = which or shutil.which
        self._popen = popen or subprocess.Popen
        self._proc: subprocess.Popen | None = None
        self.last_error: str | None = None
        self.last_stdout: str | None = None
        self.last_stderr: str | None = None
        self.last_returncode: int | None = None
        self.last_argv: list[str] | None = None
    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        max_output_chars: int = _MAX_OUTPUT_CHARS,
        max_output_words: int = _MAX_OUTPUT_WORDS,
        max_output_lines: int = 2,
        join_lines: bool = False,
        keep_newlines: bool = False,
        temperature: float | None = None,
        top_p: float | None = None,
        repeat_penalty: float | None = None,
    ) -> str | None:
        binary = self._which(self._config.ai_binary)
        if not binary:
            self.last_error = "binary_missing"
            return None
        model = self._config.model_file()
        if not model.is_file():
            self.last_error = "model_missing"
            return None
        argv = self._argv(
            binary,
            model,
            prompt,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            temperature=temperature,
            top_p=top_p,
            repeat_penalty=repeat_penalty,
        )
        self.last_argv = list(argv)
        self.last_stdout = None
        self.last_stderr = None
        self.last_returncode = None
        try:
            proc = self._popen(
                ["nice", "-n", "10", *argv],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            self.last_error = f"spawn_failed:{exc}"
            return None
        self._proc = proc
        try:
            stdout, stderr = proc.communicate(timeout=self._config.ai_timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            self.last_error = "timeout"
            self.last_stdout = ""
            self.last_stderr = "timeout"
            self.last_returncode = None
            return None
        except Exception as exc:
            _kill_process_group(proc)
            self.last_error = f"failed:{exc}"
            return None
        finally:
            if proc.poll() is None:
                _kill_process_group(proc)
            self._proc = None
        self.last_stdout = stdout or ""
        self.last_stderr = stderr or ""
        self.last_returncode = proc.returncode
        validated, parse_reason = validate_ai_output_with_reason(
            stdout or "",
            max_chars=max_output_chars,
            max_words=max_output_words,
            max_lines=max_output_lines,
            join_lines=join_lines,
            keep_newlines=keep_newlines,
        )
        if validated is None:
            self.last_error = parse_reason or "invalid"
            if proc.returncode not in (0, None) and parse_reason not in {"empty_stdout"}:
                self.last_error = f"{parse_reason} (exit {proc.returncode})"
            return None
        self.last_error = None
        return validated
    def close(self) -> None:
        proc = self._proc
        if proc is not None:
            _kill_process_group(proc)
            self._proc = None
    def _argv(
        self,
        binary: str,
        model: Path,
        prompt: str,
        *,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        repeat_penalty: float | None = None,
    ) -> list[str]:
        tokens = self._config.ai_max_tokens if max_tokens is None else max_tokens
        argv = [
            binary,
            "-m",
            str(model),
            "-ngl",
            str(self._config.ai_ngl),
            "-c",
            str(max(256, self._config.ai_context_size)),
            "-n",
            str(max(8, int(tokens))),
            "-b",
            str(max(16, self._config.ai_batch_size)),
            "-ub",
            str(max(16, self._config.ai_batch_size)),
            "-t",
            str(max(1, self._config.ai_threads)),
            "--no-display-prompt",
            "--single-turn",
            "--no-warmup",
            "--simple-io",
            "--log-disable",
            "--no-jinja",
            "--no-perf",
        ]
        if temperature is not None:
            argv.extend(["--temp", f"{float(temperature):.3f}"])
        if top_p is not None:
            argv.extend(["--top-p", f"{float(top_p):.3f}"])
        if repeat_penalty is not None:
            argv.extend(["--repeat-penalty", f"{float(repeat_penalty):.3f}"])
        argv.extend(
            [
                "-p",
                _instruct_prompt(prompt, system_prompt=system_prompt),
            ]
        )
        return argv

def generate_briefing_text(
    prompt: str,
    *,
    config: WatcherConfig,
    generator: LocalTextGenerator | None,
    use_ai: bool,
    fallback: str,
) -> tuple[str, str]:
    if not use_ai or generator is None:
        return fallback, "fallback"
    generated = generator.generate(
        prompt,
        max_tokens=max(config.briefing_max_tokens, config.ai_max_tokens),
        system_prompt=_BRIEFING_SYSTEM_PROMPT,
        max_output_chars=_BRIEFING_OUTPUT_CHARS,
        max_output_words=_BRIEFING_OUTPUT_WORDS,
        max_output_lines=3,
        join_lines=True,
        temperature=config.briefing_temperature,
        top_p=config.briefing_top_p,
        repeat_penalty=config.briefing_repeat_penalty,
    )
    if generated:
        return generated, "ai"
    return fallback, "fallback"

def generate_reminder_text(
    decision: ReminderDecision,
    activity: ActivitySnapshot,
    *,
    now,
    config: WatcherConfig,
    generator: LocalTextGenerator | None,
    use_ai: bool,
    state: ReminderState | None = None,
) -> tuple[list[str], str]:
    snapshot = decision.snapshot
    title = snapshot.title if snapshot is not None else "esa tarea"
    seed = snapshot.id if snapshot is not None else title
    fallback = [fallback_phrase(title, seed=seed)]
    if not use_ai or generator is None:
        return fallback, "fallback"

    prompt = build_reminder_prompt(decision, activity, now=now, state=state)
    print(f"Task watcher: reminder prompt ({len(prompt)} chars)")
    for line in prompt.splitlines():
        if line.strip().startswith("último_mensaje:") or "menciones_hoy:" in line:
            print(f"Task watcher: {line.strip()}")
    generated = generator.generate(
        prompt,
        max_tokens=config.ai_max_tokens,
        system_prompt=_REMINDER_SYSTEM_PROMPT,
        max_output_chars=_REMINDER_BATCH_CHARS,
        max_output_words=_REMINDER_BATCH_WORDS,
        max_output_lines=_REMINDER_BATCH_LINES,
        join_lines=True,
        keep_newlines=True,
    )
    if generated:
        messages = parse_reminder_messages(generated)
        if messages:
            return messages, "ai"
        # join_lines path may have flattened delimiters; try the raw stdout.
        raw = getattr(generator, "last_stdout", None) or generated
        messages = parse_reminder_messages(raw)
        if messages:
            return messages, "ai"
    return fallback, "fallback"
