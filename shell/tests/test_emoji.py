from __future__ import annotations

from shell.servicios.emojis.catalogo import EmojiRecord, load_emojis, search_emojis


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


def _run() -> None:
    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
