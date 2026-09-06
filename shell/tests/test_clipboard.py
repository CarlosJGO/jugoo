from __future__ import annotations

from pathlib import Path

from shell.servicios.portapapeles.historia import (
    ClipboardEntry,
    ClipboardHistory,
    format_copied_ago,
    load_history,
    preview_text,
    save_history,
    search_entries,
)
from shell.servicios.portapapeles.servicio import ClipboardService, copy_text, paste_text_to_window
from shell.eventbus import EventBus


def _failing_watch():
    raise OSError("no watch")


def test_add_entry_keeps_newest_first() -> None:
    history = ClipboardHistory(limit=10)
    assert history.remember("alpha", now=1.0)
    assert history.remember("beta", now=2.0)
    assert [item.text for item in history.entries] == ["beta", "alpha"]


def test_consecutive_duplicate_is_ignored() -> None:
    history = ClipboardHistory()
    assert history.remember("same", now=1.0)
    assert history.remember("same", now=2.0) is False
    assert len(history.entries) == 1
    assert history.entries[0].copied_at == 1.0


def test_existing_duplicate_is_promoted() -> None:
    history = ClipboardHistory()
    history.remember("one", now=1.0)
    history.remember("two", now=2.0)
    history.remember("one", now=3.0)
    assert [item.text for item in history.entries] == ["one", "two"]
    assert history.entries[0].copied_at == 3.0


def test_limit_drops_oldest() -> None:
    history = ClipboardHistory(limit=3)
    history.remember("a", now=1.0)
    history.remember("b", now=2.0)
    history.remember("c", now=3.0)
    history.remember("d", now=4.0)
    assert [item.text for item in history.entries] == ["d", "c", "b"]


def test_search_unicode_and_multiline() -> None:
    entries = (
        ClipboardEntry("1", "sudo pacman -Syu", 1.0),
        ClipboardEntry("2", "Hola, ¿cómo estás?\nsegunda línea", 2.0),
        ClipboardEntry("3", "https://github.com/example", 3.0),
    )
    assert [item.id for item in search_entries(entries, "pacman")] == ["1"]
    assert [item.id for item in search_entries(entries, "cómo")] == ["2"]
    assert [item.id for item in search_entries(entries, "SEGUNDA")] == ["2"]
    assert [item.id for item in search_entries(entries, "github")] == ["3"]
    assert search_entries(entries, "") == entries


def test_preview_truncates_without_touching_payload() -> None:
    text = "primera\nsegunda\ntercera\n" + ("x" * 400)
    preview = preview_text(text, max_chars=40, max_lines=2)
    assert "primera" in preview
    assert "…" in preview
    assert text.endswith("x" * 400)
    assert preview != text


def test_long_content_is_stored_whole_until_byte_limit() -> None:
    history = ClipboardHistory(max_item_bytes=256)
    long_text = "ñ" * 20 + "\n" + "comando largo " * 3
    assert len(long_text.encode("utf-8")) <= 256
    assert history.remember(long_text, now=1.0)
    assert history.entries[0].text == long_text
    too_big = "y" * 400
    assert history.remember(too_big, now=2.0) is False
    assert history.entries[0].text == long_text


def test_selection_returns_full_text() -> None:
    history = ClipboardHistory()
    history.remember("visible corto", now=1.0)
    history.remember("contenido\ncompleto", now=2.0)
    selected = history.entry_by_id(history.entries[0].id)
    assert selected is not None
    assert selected.text == "contenido\ncompleto"


def test_relative_time_copy_label() -> None:
    assert format_copied_ago(100.0, now=104.0) == "Copiado ahora"
    assert format_copied_ago(100.0, now=160.0) == "Copiado hace 1 minuto"
    assert format_copied_ago(100.0, now=100.0 + 8 * 60) == "Copiado hace 8 minutos"


def test_persistence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "clipboard-history.json"
    history = ClipboardHistory()
    history.remember("https://example.org/á", now=10.0)
    history.remember("línea 1\nlínea 2", now=11.0)
    save_history(path, history.entries)
    loaded = load_history(path)
    assert [item.text for item in loaded] == ["línea 1\nlínea 2", "https://example.org/á"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_service_copy_selects_without_logging_payload(tmp_path: Path) -> None:
    copied: list[str] = []
    bus = EventBus()
    service = ClipboardService(
        bus,
        path=tmp_path / "clipboard-history.json",
        paster=lambda: None,
        copier=lambda text: copied.append(text) or True,
        watch_factory=_failing_watch,
        clock=lambda: 42.0,
    )
    service.start()
    service.remember_text("secreto 🔐", now=1.0)
    entry = service.entries[0]
    assert service.copy_entry(entry.id) is True
    assert copied == ["secreto 🔐"]
    service.close()


def test_copy_text_sends_payload_only_on_stdin() -> None:
    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")
        return _Result()

    assert copy_text("no-log", runner=runner) is True
    assert seen["command"][0] == "wl-copy"
    assert seen["input"] == "no-log"


def test_paste_text_to_window_sends_shortcut_to_address() -> None:
    seen: list[object] = []

    class _Result:
        returncode = 0

    def runner(command, **kwargs):
        seen.append(command)
        return _Result()

    assert paste_text_to_window("0x123", runner=runner) is True
    assert seen == [
        [
            "hyprctl",
            "dispatch",
            'hl.dsp.focus({ window = "address:0x123" })',
        ],
        ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"],
    ]


def _run() -> None:
    import inspect
    import tempfile

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        parameters = inspect.signature(test).parameters
        if "tmp_path" in parameters:
            with tempfile.TemporaryDirectory() as folder:
                test(Path(folder))
        else:
            test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
