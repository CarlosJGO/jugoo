"""Free-form ask: user text → llama-cli → assistant notification."""

from __future__ import annotations

import re
from typing import Any, Callable

from ...models import ASSISTANT_SOURCE_AI, ASSISTANT_SOURCE_FALLBACK
from .chat_memory import (
    append_chat_turn,
    build_prompt_with_memory,
    clear_chat_memory,
    load_chat_memory,
)
from .vigilancia.config import WatcherConfig
from .vigilancia.ia import LocalTextGenerator

# Conversational assistant — NOT the short-notification voice used for reminders.
_CHAT_SYSTEM_PROMPT = (
    "Eres Jugoo, el compañero de escritorio del usuario. "
    "Hablas en español, cercano y natural, como en un chat de verdad. "
    "Responde con claridad y sustancia: explica, da razones, matices o pasos "
    "cuando ayuden. No te limites a una frase corta salvo que la pregunta lo pida. "
    "Si hay historial, continúa la conversación sin repetir lo obvio. "
    "Si no sabes algo, dilo sin inventar. No repitas el mensaje del usuario. "
    "No digas que eres un modelo ni menciones reglas internas."
)

_FALLBACK_BODY = "No pude consultar al modelo ahora. Revisa la IA en Configuraciones."
_RESET_ACK = "Conversación reiniciada. Cuando quieras, dime qué necesitas."

# Room for a real chat reply (reminders stay on the short token budget).
_ASK_MAX_TOKENS = 512
_ASK_MAX_OUTPUT_CHARS = 3200
_ASK_MAX_OUTPUT_WORDS = 560
_ASK_MAX_OUTPUT_LINES = 48

_CHATML_CHUNK = re.compile(r"<\|[^>]+\|>")
_ASSISTANT_HEADER = re.compile(
    r"<\|start_header_id\|>\s*assistant\s*<\|end_header_id\|>",
    re.I,
)
# llama-cli interactive help often lands in stdout as one mangled line.
_HELP_BLOCK = re.compile(
    r"(?is)['\"]?/?regen\b.*?globbing pattern\s*"
    r"|regenerate the last response.*?globbing pattern\s*"
    r"|available commands:.*?(?=(?:>|\n\n\[ Prompt:)|$)"
    r"|using custom system prompt\s*"
)
_ROLE_ECHO = re.compile(
    r"(?is)(?:^|\s)(?:>\s*)?(?:system|user|assistant)\b\s*"
)
_PERF_LINE = re.compile(r"(?im)^\s*\[?\s*Prompt:.*?Generation:.*?\]?\s*$")
_PROMPT_ECHO_LINE = re.compile(
    # Only the interactive "> user text" line, never a whole one-line stdout dump.
    r"(?im)^\s*>\s*(?!system\b)\S.{0,160}$"
)
_EXITING = re.compile(r"(?i)\bexiting\b\.+")
_NOISE_TOKEN = re.compile(
    r"(?i)\b(?:"
    r"llama_|ggml_|gguf_|print_info|system_info|sampler|n_keep|n_predict|"
    r"token/s|exiting|globbing|regenerate|/clear|/read|/glob|/regen|"
    r"stop or exit|clear the chat history"
    r")\b"
)
_REPLY_START = re.compile(
    r"(?:¡\s*(?:Hola|Hey|Buenas|Buenos|Claro)|"
    r"(?<![A-Za-zÁÉÍÓÚáéíóúñÑ])(?:Hola|Claro|Por supuesto|Vale|Perfecto|Entendido|"
    r"De acuerdo|Sí|Ok)\b|"
    r"¿)",
)


def normalize_user_ask(user_text: str) -> str:
    return " ".join(str(user_text or "").split()).strip()


def parse_ask_command(user_text: str) -> tuple[bool, str]:
    """Return ``(reset_conversation, message)``.

    ``/new`` alone clears memory. ``/new hola`` clears then asks ``hola``.
    """
    cleaned = normalize_user_ask(user_text)
    if not cleaned:
        return False, ""
    lowered = cleaned.casefold()
    if lowered == "/new":
        return True, ""
    if lowered.startswith("/new ") or lowered.startswith("/new\t"):
        return True, cleaned[4:].strip()
    return False, cleaned


def is_prompt_echo(user_text: str, reply: str) -> bool:
    """True when the model (or parser) returned the user text instead of an answer."""
    user = normalize_user_ask(user_text).casefold()
    text = " ".join(str(reply or "").split()).strip().casefold()
    if not user or not text:
        return True
    if text == user:
        return True
    if text.rstrip(" .!?…") == user:
        return True
    if text.startswith(user):
        remainder = text[len(user) :].strip(" :—-.!?…")
        if not remainder or len(remainder) <= 2:
            return True
    return False


def _normalize_paragraphs(text: str) -> str:
    """Keep paragraph breaks; flatten only accidental single newlines inside a sentence."""
    chunks = re.split(r"\n\s*\n+", text.replace("\r\n", "\n"))
    paragraphs: list[str] = []
    for chunk in chunks:
        line = " ".join(chunk.split())
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs).strip()


def extract_chat_reply(
    raw: str,
    *,
    user_text: str,
    system_prompt: str = _CHAT_SYSTEM_PROMPT,
) -> str | None:
    """Keep only the assistant's spoken reply; drop llama help + prompt echo."""
    if not raw or not raw.strip():
        return None

    text = raw
    if "... (truncated)" in text:
        text = text.rsplit("... (truncated)", 1)[-1]

    matches = list(_ASSISTANT_HEADER.finditer(text))
    if matches:
        text = text[matches[-1].end() :]

    text = _CHATML_CHUNK.sub(" ", text)
    text = _HELP_BLOCK.sub(" ", text)
    text = _PERF_LINE.sub(" ", text)
    text = _PROMPT_ECHO_LINE.sub(" ", text)
    text = _EXITING.sub(" ", text)

    sys_marker = (system_prompt or "").strip()[:40]
    if sys_marker and sys_marker in text:
        text = text.split(sys_marker, 1)[-1]
        cut = re.search(r"(?is)\buser\b\s*", text)
        if cut:
            text = text[cut.end() :]

    user = normalize_user_ask(user_text)
    if user:
        for size in (min(80, len(user)), min(40, len(user)), min(20, len(user))):
            if size < 8:
                break
            needle = user[:size]
            idx = text.rfind(needle)
            if idx < 0:
                compact = re.sub(r"[^a-záéíóúñ0-9 ]+", "", needle.casefold())
                hay = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text.casefold())
                pos = hay.rfind(compact[: max(12, size // 2)])
                if pos < 0:
                    continue
                reply_at = _REPLY_START.search(text, pos)
                if reply_at:
                    text = text[reply_at.start() :]
                    break
                continue
            after = text[idx + len(needle) :]
            reply_at = _REPLY_START.search(after)
            if reply_at:
                text = after[reply_at.start() :]
                break
            if len(after.strip()) > 12:
                text = after
                break

    text = _ROLE_ECHO.sub(" ", text)
    text = _normalize_paragraphs(text)

    reply_at = _REPLY_START.search(text)
    if reply_at:
        text = text[reply_at.start() :]

    lowered = text.casefold()
    for junk in (
        "eres jugoo",
        "eres el asistente",
        "responde en español",
        "hablas en español",
        "este es el historial",
        "conversación reciente",
        "no repitas el mensaje",
        "si hay historial",
        "si no sabes algo",
        "no digas que eres",
    ):
        if junk in lowered:
            parts = re.split(re.escape(junk), text, flags=re.I)
            for part in reversed(parts):
                part = part.strip(" .,:;—-")
                if _REPLY_START.search(part) and len(part) > 10:
                    text = part[_REPLY_START.search(part).start() :]
                    break

    text = _normalize_paragraphs(text)
    if _NOISE_TOKEN.search(text) and _REPLY_START.search(text):
        reply_at = _REPLY_START.search(text)
        if reply_at and reply_at.start() > 0:
            text = text[reply_at.start() :]
        text = _NOISE_TOKEN.sub(" ", text)
        text = _normalize_paragraphs(text)

    if len(text) > _ASK_MAX_OUTPUT_CHARS:
        text = text[: _ASK_MAX_OUTPUT_CHARS - 1].rstrip() + "…"
    if not text or not any(ch.isalpha() for ch in text):
        return None
    if is_prompt_echo(user_text, text):
        return None
    if text.casefold() in {"regen", "clear", "read", "glob"}:
        return None
    if "regenerate the last" in text.casefold() or "globbing" in text.casefold():
        return None
    if text.casefold().startswith("eres el asistente") or text.casefold().startswith(
        "eres jugoo"
    ):
        return None
    return text


def ask_display_timeout_ms(body: str) -> int:
    """Give longer replies more reading time without sticking forever."""
    words = max(1, len(str(body or "").split()))
    # ~0.5s/word after a short base; clamp for toast UX.
    return int(min(120_000, max(16_000, 10_000 + words * 500)))


def run_user_ask(
    user_text: str,
    *,
    notifications: Any,
    config: WatcherConfig | None = None,
    generator: LocalTextGenerator | None = None,
    on_done: Callable[[], None] | None = None,
    memory_path: Any | None = None,
) -> str:
    """Generate a reply and post it via ``post_assistant``. Returns the body posted."""
    reset, message = parse_ask_command(user_text)
    if reset:
        clear_chat_memory(path=memory_path)
        if not message:
            _post_assistant(
                notifications,
                body=_RESET_ACK,
                source=ASSISTANT_SOURCE_FALLBACK,
                on_done=on_done,
            )
            return _RESET_ACK

    cleaned = normalize_user_ask(message if reset else user_text)
    if not cleaned:
        if on_done is not None:
            on_done()
        return ""

    watcher_config = config or WatcherConfig.from_shell()
    gen = generator or LocalTextGenerator(watcher_config)
    body = ""
    source = ASSISTANT_SOURCE_FALLBACK
    history = load_chat_memory(memory_path)
    prompt = build_prompt_with_memory(cleaned, history)

    if watcher_config.ai_enabled:
        cold_timeout = max(120.0, float(watcher_config.ai_timeout_sec) * 2.5)
        token_budget = max(_ASK_MAX_TOKENS, int(watcher_config.ai_max_tokens) * 3)
        generated = gen.generate(
            prompt,
            system_prompt=_CHAT_SYSTEM_PROMPT,
            max_tokens=token_budget,
            max_output_chars=_ASK_MAX_OUTPUT_CHARS,
            max_output_words=_ASK_MAX_OUTPUT_WORDS,
            max_output_lines=_ASK_MAX_OUTPUT_LINES,
            join_lines=True,
            keep_newlines=True,
            temperature=0.75,
            top_p=0.92,
            repeat_penalty=1.06,
            timeout_sec=cold_timeout,
            # Native -sys/-p chat (no hand-rolled ChatML).
            wrap_instruct=False,
        )
        raw = getattr(gen, "last_stdout", None) or generated or ""
        extracted = extract_chat_reply(
            raw,
            user_text=cleaned,
            system_prompt=_CHAT_SYSTEM_PROMPT,
        )
        if extracted:
            body = extracted
            source = ASSISTANT_SOURCE_AI
        elif generated and not is_prompt_echo(cleaned, generated):
            body = extract_chat_reply(
                generated,
                user_text=cleaned,
                system_prompt=_CHAT_SYSTEM_PROMPT,
            ) or generated
            if body and not is_prompt_echo(cleaned, body):
                source = ASSISTANT_SOURCE_AI
            else:
                body = ""
        if not body:
            error = getattr(gen, "last_error", None) or "empty"
            print(
                f"shell: ai ask failed error={error!r} "
                f"stdout_len={len(raw)} user={cleaned!r}",
                flush=True,
            )
            if raw.strip():
                preview = raw.strip().replace("\n", "\\n")[:240]
                print(f"shell: ai ask stdout_preview={preview!r}", flush=True)

    if not body:
        body = _FALLBACK_BODY
        source = ASSISTANT_SOURCE_FALLBACK
    else:
        append_chat_turn("user", cleaned, path=memory_path)
        append_chat_turn("assistant", body, path=memory_path)

    _post_assistant(notifications, body=body, source=source, on_done=on_done)
    return body


def _post_assistant(
    notifications: Any,
    *,
    body: str,
    source: str,
    on_done: Callable[[], None] | None,
) -> None:
    def _post() -> bool:
        poster = getattr(notifications, "post_assistant", None)
        if callable(poster):
            poster(
                body=body,
                meta="te responde",
                source=source,
                expire_timeout_ms=ask_display_timeout_ms(body),
            )
        if on_done is not None:
            on_done()
        return False

    _schedule_on_gtk(_post)


def _schedule_on_gtk(callback: Callable[[], bool]) -> None:
    """Run ``callback`` on the GTK main context (or immediately in unit tests)."""
    import threading

    try:
        from gi.repository import GLib

        GLib.idle_add(callback)
        if threading.current_thread() is threading.main_thread():
            context = GLib.MainContext.default()
            if not context.is_owner():
                while context.pending():
                    context.iteration(False)
    except Exception:
        callback()
