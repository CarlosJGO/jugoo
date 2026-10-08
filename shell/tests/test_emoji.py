from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from shell.servicios.emojis.catalogo import EmojiRecord, load_emojis, search_emojis
from shell.servicios.emojis.recientes import EmojiRecentStore
from shell.widgets.pickers.emoji import _origin_in
from shell.widgets.pickers.session import scroll_to_reveal


def _catalog() -> tuple[EmojiRecord, ...]:
    return (
        EmojiRecord("😀", "grinning face", ("cheerful", "cara sonriendo", "sonrisa")),
        EmojiRecord("❤️", "red heart", ("love", "corazón", "amor")),
        EmojiRecord("👍", "thumbs up", ("yes", "+1", "aprobación")),
        EmojiRecord("🇪🇸", "flag: spain", ("es", "españa", "spain")),
        EmojiRecord("🐱", "cat face", ("gato", "animal")),
    )


def test_search_by_name() -> None:
    matches = search_emojis(_catalog(), "grinning")
    assert [item.glyph for item in matches] == ["😀"]


def test_search_by_alias_and_spanish() -> None:
    catalog = _catalog()
    assert [item.glyph for item in search_emojis(catalog, "corazón")] == ["❤️"]
    assert [item.glyph for item in search_emojis(catalog, "LOVE")] == ["❤️"]
    assert [item.glyph for item in search_emojis(catalog, "+1")] == ["👍"]
    assert [item.glyph for item in search_emojis(catalog, "españa")] == ["🇪🇸"]


def test_empty_query_returns_all() -> None:
    catalog = _catalog()
    assert search_emojis(catalog, "") == catalog
    assert search_emojis(catalog, "   ") == catalog


def test_search_results_preserve_unicode_and_zwj() -> None:
    catalog = _catalog() + (EmojiRecord("🧑‍💻", "technologist", ("developer", "programador")),)
    matches = search_emojis(catalog, "programador")
    assert matches[0].glyph == "🧑‍💻"


def test_selection_keeps_the_glyph() -> None:
    matches = search_emojis(_catalog(), "cat")
    selected = matches[0]
    assert selected.glyph == "🐱"
    assert selected.name == "cat face"


def test_unknown_query_is_empty() -> None:
    assert search_emojis(_catalog(), "xyzzy-no-emoji") == ()


def test_recent_emojis_persist_in_most_recent_order() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "recent.json"
        store = EmojiRecentStore(path, limit=3)
        catalog = _catalog()
        store.remember("😀")
        store.remember("❤️")
        store.remember("😀")

        reloaded = EmojiRecentStore(path, limit=3)
        assert [emoji.glyph for emoji in reloaded.recent(catalog)] == ["😀", "❤️"]


def test_recent_emojis_ignore_unknown_catalog_entries() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "recent.json"
        store = EmojiRecentStore(path)
        store.remember("missing")
        store.remember("🐱")

        assert [emoji.glyph for emoji in store.recent(_catalog())] == ["🐱"]


def test_local_catalog_is_substantial() -> None:
    emojis = load_emojis()
    glyphs = {item.glyph for item in emojis}
    assert len(emojis) > 500
    assert "😀" in glyphs
    heart_hits = search_emojis(emojis, "heart")
    assert any("❤" in item.glyph or "❤️" in item.glyph for item in heart_hits)
    spanish_hits = search_emojis(emojis, "sonrisa")
    grinning_hits = search_emojis(emojis, "grinning")
    assert spanish_hits or grinning_hits


class _Alloc:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


class _Node:
    def __init__(self, x: int, y: int, parent: "_Node | None" = None, translated=None) -> None:
        self._alloc = _Alloc(x, y)
        self._parent = parent
        self._translated = translated

    def translate_coordinates(self, _ancestor: object, _x: int, _y: int):
        return self._translated

    def get_allocation(self) -> _Alloc:
        return self._alloc

    def get_parent(self) -> "_Node | None":
        return self._parent


def test_scroll_follows_emoji_below_the_page() -> None:
    value = scroll_to_reveal(0, 200, 400, 36)
    assert value == 240
    assert 400 >= value
    assert 400 + 36 <= value + 200


def test_scroll_follows_emoji_above_the_page() -> None:
    assert scroll_to_reveal(300, 200, 10, 36) == 6


def test_scroll_keeps_emoji_already_visible() -> None:
    assert scroll_to_reveal(100, 200, 120, 36) == 100


def test_origin_reads_the_xy_pair() -> None:
    content = _Node(0, 0)
    cell = _Node(0, 0, content, translated=(12, 480))
    assert _origin_in(cell, content) == (12, 480)


def test_origin_sums_allocations_when_translation_is_unavailable() -> None:
    content = _Node(0, 0)
    flow = _Node(4, 40, content)
    cell = _Node(8, 120, flow)
    assert _origin_in(cell, content) == (12, 160)


def _run() -> None:
    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
