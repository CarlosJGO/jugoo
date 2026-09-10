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

from .politica import ActivitySnapshot, ReminderDecision, due_label

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

_REMINDER_SYSTEM_PROMPT = "Responde con una sola frase breve en español. Sin explicaciones."

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

) -> str:

    snapshot = decision.snapshot

    title = snapshot.title if snapshot is not None else "una tarea"

    due = due_label(snapshot, now) if snapshot is not None else "soon"

    return (

        f"Task: {title}\n"

        f"Due: {due}\n"

        f"Context: {activity.label}\n"

        "Generate one short natural reminder in Spanish.\n"

        "Maximum 20 words.\n"

        "Do not explain anything."

    )



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

) -> str | None:

    text, _reason = validate_ai_output_with_reason(

        raw,

        max_chars=max_chars,

        max_words=max_words,

        max_lines=max_lines,

        join_lines=join_lines,

    )

    return text



def validate_ai_output_with_reason(

    raw: str,

    *,

    max_chars: int = _MAX_OUTPUT_CHARS,

    max_words: int = _MAX_OUTPUT_WORDS,

    max_lines: int = 2,

    join_lines: bool = False,

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

            lines = lines[-max(1, int(max_lines)) :]

        else:

            return None, f"too_many_lines:{len(lines)}>{max_lines}"

    text = " ".join(lines) if join_lines else lines[0]

    if len(text) > max_chars:

        return None, f"too_many_chars:{len(text)}>{max_chars}"

    words = text.split()

    if not words:

        return None, "empty_after_join"

    if len(words) > max_words:

        return None, f"too_many_words:{len(words)}>{max_words}"

    if any(pattern.search(text) for pattern in _META_PATTERNS):

        return None, "meta_pattern"

    if "\n" in text:

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

) -> tuple[str, str]:

    snapshot = decision.snapshot

    title = snapshot.title if snapshot is not None else "esa tarea"

    seed = snapshot.id if snapshot is not None else title

    fallback = fallback_phrase(title, seed=seed)

    if not use_ai or generator is None:

        return fallback, "fallback"

    prompt = build_reminder_prompt(decision, activity, now=now)

    generated = generator.generate(prompt)

    if generated:

        return generated, "ai"

    return fallback, "fallback"