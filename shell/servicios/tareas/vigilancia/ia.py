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
_META_PATTERNS = (
    re.compile(r"^\s*\{"),
    re.compile(r"```"),
    re.compile(r"\bjson\b", re.I),
    re.compile(r"^\s*(sure|here(?:'s| is)|as an ai|i can|let me)\b", re.I),
    re.compile(r"^\s*(claro|por supuesto|como modelo|no puedo)\b", re.I),
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
)

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


def validate_ai_output(raw: str) -> str | None:
    if not raw or not raw.strip():
        return None
    lines = []
    for line in raw.splitlines():
        stripped = line.strip().strip('"').strip("'")
        if not stripped:
            continue
        lowered = stripped.casefold()
        if any(lowered.startswith(prefix) for prefix in _SKIP_LINE_PREFIXES):
            continue
        lines.append(stripped)
    if not lines:
        return None
    text = lines[0]
    if len(lines) > 2:
        return None
    if len(text) > _MAX_OUTPUT_CHARS:
        return None
    words = text.split()
    if not words or len(words) > _MAX_OUTPUT_WORDS:
        return None
    if any(pattern.search(text) for pattern in _META_PATTERNS):
        return None
    if "\n" in text:
        return None
    return text


def _instruct_prompt(user_prompt: str) -> str:
    return (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        "Responde con una sola frase breve en español. Sin explicaciones."
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

    def generate(self, prompt: str) -> str | None:
        binary = self._which(self._config.ai_binary)
        if not binary:
            return None
        model = self._config.model_file()
        if not model.is_file():
            return None
        argv = self._argv(binary, model, prompt)
        try:
            proc = self._popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        except OSError:
            return None
        self._proc = proc
        try:
            stdout, _stderr = proc.communicate(timeout=self._config.ai_timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            return None
        except Exception:
            _kill_process_group(proc)
            return None
        finally:
            if proc.poll() is None:
                _kill_process_group(proc)
            self._proc = None
        if proc.returncode not in (0, None):
            # Some llama-cli builds still print the answer then exit non-zero.
            validated = validate_ai_output(stdout or "")
            return validated
        return validate_ai_output(stdout or "")

    def close(self) -> None:
        proc = self._proc
        if proc is not None:
            _kill_process_group(proc)
            self._proc = None

    def _argv(self, binary: str, model: Path, prompt: str) -> list[str]:
        return [
            binary,
            "-m",
            str(model),
            "-ngl",
            str(self._config.ai_ngl),
            "-c",
            str(max(256, self._config.ai_context_size)),
            "-n",
            str(max(8, self._config.ai_max_tokens)),
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
            "-p",
            _instruct_prompt(prompt),
        ]


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
