from __future__ import annotations

import json
from pathlib import Path

from shell.servicios.portapapeles.historia import (
    ClipboardEntry,
    ClipboardHistory,
    ENTRY_IMAGE,
    HISTORY_VERSION,
    format_copied_ago,
    hash_bytes,
    load_history,
    load_history_result,
    preview_text,
    save_history,
    search_entries,
)
from shell.servicios.portapapeles.servicio import (
    ClipboardService,
    copy_image_bytes,
    copy_text,
    normalize_image_to_png,
    paste_text_to_window,
    preferred_image_mime,
)
from shell.eventbus import EventBus


def _failing_watch():
    raise OSError("no watch")


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0), size: int = 8) -> bytes:
    """Minimal solid PNG via GdkPixbuf."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    r, g, b = color
    packed = bytes([r, g, b] * (size * size))
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(packed),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        size,
        size,
        size * 3,
    )
    ok, buffer = pixbuf.save_to_bufferv("png", [], [])
    assert ok and buffer
    return bytes(buffer)


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
        ClipboardEntry(id="1", text="sudo pacman -Syu", copied_at=1.0),
        ClipboardEntry(id="2", text="Hola, ¿cómo estás?\nsegunda línea", copied_at=2.0),
        ClipboardEntry(id="3", text="https://github.com/example", copied_at=3.0),
        ClipboardEntry(
            id="4",
            kind=ENTRY_IMAGE,
            mime="image/png",
            path="clipboard/images/abcd.png",
            copied_at=4.0,
        ),
    )
    assert [item.id for item in search_entries(entries, "pacman")] == ["1"]
    assert [item.id for item in search_entries(entries, "cómo")] == ["2"]
    assert [item.id for item in search_entries(entries, "SEGUNDA")] == ["2"]
    assert [item.id for item in search_entries(entries, "github")] == ["3"]
    assert [item.id for item in search_entries(entries, "imagen")] == ["4"]
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


def test_oversized_text_is_discarded_from_history_only() -> None:
    history = ClipboardHistory(max_text_bytes=64)
    assert history.remember("ok", now=1.0)
    assert history.remember("z" * 200, now=2.0) is False
    assert [item.text for item in history.entries] == ["ok"]


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
    history = ClipboardHistory(data_dir=tmp_path)
    history.remember("https://example.org/á", now=10.0)
    history.remember("línea 1\nlínea 2", now=11.0)
    save_history(path, history.entries)
    loaded = load_history(path)
    assert [item.text for item in loaded] == ["línea 1\nlínea 2", "https://example.org/á"]
    assert path.stat().st_mode & 0o777 == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == HISTORY_VERSION


def test_v1_history_loads_and_migrates(tmp_path: Path) -> None:
    path = tmp_path / "clipboard-history.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "id": "1789000023781-413",
                        "text": "/home/carlosjgo/.local/share/waybar-shell/settings.json",
                        "copied_at": 1789000023.7816327,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_history_result(path)
    assert loaded.trusted is True
    assert loaded.version == 1
    assert len(loaded.entries) == 1
    assert loaded.entries[0].is_text
    assert loaded.entries[0].text.endswith("settings.json")

    bus = EventBus()
    service = ClipboardService(
        bus,
        path=path,
        data_dir=tmp_path,
        paster=lambda: None,
        list_types=lambda: (),
        watch_factory=_failing_watch,
        clock=lambda: 42.0,
    )
    service.start()
    assert service.entries[0].text.endswith("settings.json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == HISTORY_VERSION
    service.close()


def test_corrupt_json_does_not_wipe_images(tmp_path: Path) -> None:
    path = tmp_path / "clipboard-history.json"
    images = tmp_path / "clipboard" / "images"
    images.mkdir(parents=True)
    orphan = images / "keep-me.png"
    orphan.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    path.write_text("{not-json", encoding="utf-8")
    loaded = load_history_result(path)
    assert loaded.trusted is False
    assert loaded.entries == ()

    bus = EventBus()
    service = ClipboardService(
        bus,
        path=path,
        data_dir=tmp_path,
        images_dir=images,
        paster=lambda: None,
        list_types=lambda: (),
        watch_factory=_failing_watch,
    )
    service.start()
    assert orphan.is_file()
    service.close()


def test_image_remember_dedup_and_byte_budget(tmp_path: Path) -> None:
    history = ClipboardHistory(
        limit=10,
        max_history_bytes=2500,
        data_dir=tmp_path,
        images_dir=tmp_path / "clipboard" / "images",
    )
    red = _png_bytes((255, 0, 0), size=16)
    blue = _png_bytes((0, 0, 255), size=16)
    green = _png_bytes((0, 255, 0), size=32)
    assert history.remember_image(red, mime="image/png", now=1.0)
    assert history.remember_image(red, mime="image/png", now=2.0) is False
    assert len(history.entries) == 1
    assert history.remember_image(blue, mime="image/png", now=3.0)
    assert history.remember_image(green, mime="image/png", now=4.0)
    # Promote red again — same file reused.
    assert history.remember_image(red, mime="image/png", now=5.0)
    assert history.entries[0].content_hash == hash_bytes(red)
    paths = {entry.path for entry in history.entries if entry.is_image}
    for relative in paths:
        assert (tmp_path / relative).is_file()


def test_history_byte_limit_drops_oldest_images(tmp_path: Path) -> None:
    history = ClipboardHistory(
        limit=50,
        max_history_bytes=1800,
        data_dir=tmp_path,
        images_dir=tmp_path / "clipboard" / "images",
    )
    first = _png_bytes((10, 10, 10), size=24)
    second = _png_bytes((20, 20, 20), size=24)
    third = _png_bytes((30, 30, 30), size=24)
    history.remember_image(first, mime="image/png", now=1.0)
    history.remember_image(second, mime="image/png", now=2.0)
    history.remember_image(third, mime="image/png", now=3.0)
    hashes = [entry.content_hash for entry in history.entries]
    assert hash_bytes(third) in hashes
    # Oldest should be gone once budget is exceeded.
    assert history.total_bytes() <= 1800
    first_path = tmp_path / "clipboard" / "images" / f"{hash_bytes(first)}.png"
    if hash_bytes(first) not in hashes:
        assert not first_path.is_file()


def test_remove_image_deletes_file(tmp_path: Path) -> None:
    history = ClipboardHistory(
        data_dir=tmp_path,
        images_dir=tmp_path / "clipboard" / "images",
    )
    payload = _png_bytes((1, 2, 3), size=12)
    history.remember_image(payload, mime="image/png", now=1.0)
    entry = history.entries[0]
    absolute = tmp_path / entry.path
    assert absolute.is_file()
    assert history.remove_entry(entry.id) is True
    assert not absolute.is_file()
    assert history.entries == ()


def test_orphan_cleanup_on_trusted_load(tmp_path: Path) -> None:
    images = tmp_path / "clipboard" / "images"
    images.mkdir(parents=True)
    keep = _png_bytes((9, 9, 9), size=10)
    history = ClipboardHistory(data_dir=tmp_path, images_dir=images)
    history.remember_image(keep, mime="image/png", now=1.0)
    orphan = images / "deadbeefdeadbeef.png"
    orphan.write_bytes(b"orphan")
    assert history.cleanup_orphans() >= 1
    assert not orphan.is_file()
    assert (tmp_path / history.entries[0].path).is_file()


def test_service_prefers_image_over_text(tmp_path: Path) -> None:
    png = _png_bytes((40, 50, 60), size=12)
    bus = EventBus()
    service = ClipboardService(
        bus,
        path=tmp_path / "clipboard-history.json",
        data_dir=tmp_path,
        paster=lambda: "should-not-store",
        list_types=lambda: ("image/png", "text/plain"),
        paste_image=lambda mime: png if mime == "image/png" else None,
        watch_factory=_failing_watch,
        clock=lambda: 7.0,
    )
    service.start()
    assert len(service.entries) == 1
    assert service.entries[0].is_image
    service.close()


def test_service_copy_selects_without_logging_payload(tmp_path: Path) -> None:
    copied: list[str] = []
    bus = EventBus()
    service = ClipboardService(
        bus,
        path=tmp_path / "clipboard-history.json",
        data_dir=tmp_path,
        paster=lambda: None,
        list_types=lambda: (),
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


def test_service_copy_image_entry(tmp_path: Path) -> None:
    png = _png_bytes((70, 80, 90), size=10)
    copied: list[tuple[bytes, str]] = []
    bus = EventBus()
    service = ClipboardService(
        bus,
        path=tmp_path / "clipboard-history.json",
        data_dir=tmp_path,
        paster=lambda: None,
        list_types=lambda: (),
        copy_image=lambda payload, mime: copied.append((payload, mime)) or True,
        watch_factory=_failing_watch,
        clock=lambda: 9.0,
    )
    service.start()
    assert service.remember_image(png, mime="image/png", now=1.0)
    entry = service.entries[0]
    assert service.copy_entry(entry.id) is True
    assert copied and copied[0][1] == "image/png"
    assert copied[0][0].startswith(b"\x89PNG")
    service.close()


def test_preferred_image_mime_order() -> None:
    assert preferred_image_mime(("text/plain", "image/jpeg")) == "image/jpeg"
    assert preferred_image_mime(("image/png", "image/jpeg")) == "image/png"
    assert preferred_image_mime(("text/plain",)) is None


def test_normalize_jpeg_to_png() -> None:
    # Build a tiny JPEG via pixbuf then convert.
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    packed = bytes([10, 20, 30] * 16)
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(packed),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        4,
        4,
        12,
    )
    ok, jpeg = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    assert ok
    png = normalize_image_to_png(bytes(jpeg), "image/jpeg")
    assert png is not None
    assert png.startswith(b"\x89PNG")


def test_copy_text_sends_payload_only_on_stdin() -> None:
    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")
        seen["stdout"] = kwargs.get("stdout")
        return _Result()

    assert copy_text("no-log", runner=runner) is True
    assert seen["command"][0] == "wl-copy"
    assert seen["input"] == "no-log"
    assert seen["stdout"] is not None


def test_copy_image_bytes_uses_binary_stdin() -> None:
    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")
        return _Result()

    payload = b"\x89PNG\r\n\x1a\n"
    assert copy_image_bytes(payload, mime="image/png", runner=runner) is True
    assert seen["command"] == ["wl-copy", "--type", "image/png"]
    assert seen["input"] == payload


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
