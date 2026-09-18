"""Tests for free-form AI ask → assistant notification + chat memory."""

from __future__ import annotations

from pathlib import Path

from shell.actions import dispatch_action, resolve_actions_from_argv
from shell.models import ASSISTANT_SOURCE_AI, ASSISTANT_SOURCE_FALLBACK
from shell.servicios.tareas.ask import (
    ask_display_timeout_ms,
    extract_chat_reply,
    is_prompt_echo,
    normalize_user_ask,
    parse_ask_command,
    run_user_ask,
)
from shell.servicios.tareas.chat_memory import (
    append_chat_turn,
    build_prompt_with_memory,
    clear_chat_memory,
    load_chat_memory,
)
from shell.servicios.tareas.vigilancia.config import WatcherConfig


class _FakeNotifications:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post_assistant(self, **kwargs) -> None:
        self.posts.append(kwargs)


class _FakeGenerator:
    def __init__(self, text: str | None, *, stdout: str | None = None) -> None:
        self.text = text
        self.prompts: list[str] = []
        self.last_stdout = stdout if stdout is not None else (text or "")
        self.last_error = None if text else "empty"

    def generate(self, prompt: str, **_kwargs) -> str | None:
        self.prompts.append(prompt)
        return self.text


def test_resolve_ask_action() -> None:
    assert resolve_actions_from_argv(["action", "ask"]) == ("ask",)
    assert resolve_actions_from_argv(["--toggle-ask"]) == ("ask",)


def test_dispatch_ask_calls_toggle() -> None:
    calls: list[str] = []

    class Shell:
        def toggle_ai_prompt(self) -> None:
            calls.append("ask")

    assert dispatch_action("ask", Shell()) is None
    assert calls == ["ask"]


def test_parse_new_command() -> None:
    assert parse_ask_command("/new") == (True, "")
    assert parse_ask_command("/new hola") == (True, "hola")
    assert parse_ask_command("hola") == (False, "hola")


def test_extract_allows_claro_replies() -> None:
    reply = extract_chat_reply(
        "Claro, puedo ayudarte con eso ahora mismo.",
        user_text="ayúdame",
    )
    assert reply is not None
    assert "ayudarte" in reply.casefold()


def test_extract_strips_llama_help_and_prompt_echo() -> None:
    raw = (
        "'regen regenerate the last response /clear clear the "
        "chat history /read <file> add a text file /glob <pattern> add "
        "text files using globbing pattern > system Eres el asistente de "
        "Jugoo. Responde en español, natural y breve (unas pocas frases). "
        "Si hay historial, continúa la conversación. Si no sabes algo, dilo sin "
        "inventar. No repitas el mensaje del usuario.user holaaa ahora - "
        "podemos habigr m... facil, porque puedo escribirte sin tener que "
        "abrirte en una hnyiul.“hhut ¡Hola! Claro, podemos conversar "
        "de manera más fácil. ¿Cuál es tu pregunta o tema preferido hoy?"
    )
    reply = extract_chat_reply(
        raw,
        user_text="holaaa ahora podemos hablar mas facil",
    )
    assert reply is not None
    assert "conversar" in reply.casefold() or "hola" in reply.casefold()
    assert "regenerate" not in reply.casefold()
    assert "/clear" not in reply.casefold()
    assert "eres el asistente" not in reply.casefold()
    assert "globbing" not in reply.casefold()
    assert "regen" not in reply.casefold()


def test_extract_skips_llama_noise_and_echo() -> None:
    raw = (
        "llama_model_loader: loaded\n"
        "ayúdame\n"
        "Por supuesto, aquí va una respuesta útil.\n"
    )
    reply = extract_chat_reply(raw, user_text="ayúdame")
    assert reply is not None
    assert "por supuesto" in reply.casefold()
    assert not is_prompt_echo("ayúdame", reply)


def test_chat_memory_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "ai_chat.json"
    append_chat_turn("user", "hola", path=path)
    append_chat_turn("assistant", "hola, ¿qué tal?", path=path)
    history = load_chat_memory(path)
    assert len(history) == 2
    prompt = build_prompt_with_memory("segunda", history)
    assert "Conversación reciente:" in prompt
    assert "Usuario: hola" in prompt
    assert "Jugoo: hola, ¿qué tal?" in prompt
    assert "Usuario: segunda" in prompt
    clear_chat_memory(path=path)
    assert load_chat_memory(path) == ()


def test_run_user_ask_uses_memory_and_new(tmp_path: Path) -> None:
    path = tmp_path / "ai_chat.json"
    notifications = _FakeNotifications()
    config = WatcherConfig(ai_enabled=True)
    generator = _FakeGenerator(
        None,
        stdout="Claro, seguimos con lo de antes.",
    )
    # Seed history
    append_chat_turn("user", "tema A", path=path)
    append_chat_turn("assistant", "ok tema A", path=path)

    body = run_user_ask(
        "y ahora?",
        notifications=notifications,
        config=config,
        generator=generator,
        memory_path=path,
    )
    assert "seguimos" in body.casefold()
    assert "Conversación reciente:" in generator.prompts[0]
    assert "tema A" in generator.prompts[0]
    assert notifications.posts[0]["source"] == ASSISTANT_SOURCE_AI
    assert notifications.posts[0]["meta"] == "te responde"
    assert notifications.posts[0]["expire_timeout_ms"] >= 16_000
    assert len(load_chat_memory(path)) == 4

    notifications.posts.clear()
    reset_body = run_user_ask(
        "/new",
        notifications=notifications,
        config=config,
        generator=generator,
        memory_path=path,
    )
    assert "reiniciada" in reset_body.casefold()
    assert load_chat_memory(path) == ()


def test_system_prompt_is_conversational() -> None:
    from shell.servicios.tareas import ask as ask_mod

    prompt = ask_mod._CHAT_SYSTEM_PROMPT.casefold()
    assert "breve" not in prompt
    assert "unas pocas frases" not in prompt
    assert "chat" in prompt or "compañero" in prompt
    assert ask_mod._ASK_MAX_TOKENS >= 512


def test_extract_keeps_paragraphs() -> None:
    raw = (
        "Hacer ejercicio puede ayudar.\n\n"
        "1. Baja el estrés.\n\n"
        "2. Mejora el ánimo.\n\n"
        "Si la frustración es intensa, busca ayuda."
    )
    reply = extract_chat_reply(raw, user_text="me quita la frustración el ejercicio?")
    assert reply is not None
    assert "\n\n" in reply
    assert "estrés" in reply.casefold()
    assert ask_display_timeout_ms(reply) > ask_display_timeout_ms("ok")


def test_run_user_ask_new_with_message(tmp_path: Path) -> None:
    path = tmp_path / "ai_chat.json"
    append_chat_turn("user", "viejo", path=path)
    notifications = _FakeNotifications()
    config = WatcherConfig(ai_enabled=True)
    generator = _FakeGenerator(None, stdout="Empezamos de cero.")
    body = run_user_ask(
        "/new hola otra vez",
        notifications=notifications,
        config=config,
        generator=generator,
        memory_path=path,
    )
    assert "cero" in body.casefold()
    assert "viejo" not in generator.prompts[0]
    assert "hola otra vez" in generator.prompts[0]
    history = load_chat_memory(path)
    assert history[0].content == "hola otra vez"


def test_run_user_ask_posts_ai_reply(tmp_path: Path) -> None:
    notifications = _FakeNotifications()
    config = WatcherConfig(ai_enabled=True)
    body = run_user_ask(
        "qué hora es",
        notifications=notifications,
        config=config,
        generator=_FakeGenerator(
            None,
            stdout="Son las tres de la tarde en tu zona.",
        ),
        memory_path=tmp_path / "ai_chat.json",
    )
    assert "tres" in body
    assert notifications.posts[0]["source"] == ASSISTANT_SOURCE_AI


def test_run_user_ask_fallback_when_model_silent(tmp_path: Path) -> None:
    notifications = _FakeNotifications()
    config = WatcherConfig(ai_enabled=True)
    body = run_user_ask(
        "hola",
        notifications=notifications,
        config=config,
        generator=_FakeGenerator(None, stdout=""),
        memory_path=tmp_path / "ai_chat.json",
    )
    assert body
    assert notifications.posts[0]["source"] == ASSISTANT_SOURCE_FALLBACK


def test_run_user_ask_ignores_blank() -> None:
    notifications = _FakeNotifications()
    assert run_user_ask("   ", notifications=notifications) == ""
    assert notifications.posts == []
    assert normalize_user_ask("  a  b ") == "a b"


if __name__ == "__main__":
    import tempfile

    test_resolve_ask_action()
    test_dispatch_ask_calls_toggle()
    test_parse_new_command()
    test_extract_allows_claro_replies()
    test_extract_strips_llama_help_and_prompt_echo()
    test_extract_skips_llama_noise_and_echo()
    test_system_prompt_is_conversational()
    test_extract_keeps_paragraphs()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        test_chat_memory_roundtrip(root)
        test_run_user_ask_uses_memory_and_new(root)
        test_run_user_ask_new_with_message(root)
        test_run_user_ask_posts_ai_reply(root)
        test_run_user_ask_fallback_when_model_silent(root)
    test_run_user_ask_ignores_blank()
    print("ai ask OK")
