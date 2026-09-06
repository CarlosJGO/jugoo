"""Local emoji catalog: GTK 3 EmojiChooser data plus Unicode names. Never downloads."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

GTK_EMOJI_RESOURCE = "/org/gtk/libgtk/emoji/en.data"
GTK_EMOJI_VARIANT = "a(auss)"
GTK_EMOJI_LOCALE_DIR = Path("/usr/share/gtk-3.0/emoji")
GTK_EMOJI_TEST_CANDIDATES = (
    Path("/usr/share/unicode/emoji/emoji-test.txt"),
    Path("/usr/share/unicode-emoji/emoji-test.txt"),
)

_EMOJI_RANGES = (
    (0x231A, 0x231B),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x25FD, 0x25FE),
    (0x2614, 0x2615),
    (0x2648, 0x2653),
    (0x26AA, 0x26AB),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26F2, 0x26F3),
    (0x270A, 0x270B),
    (0x2753, 0x2755),
    (0x2795, 0x2797),
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F7E0, 0x1F7EB),
    (0x1F90C, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
)

_SINGLE_CODEPOINTS = (
    0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x2194, 0x2195, 0x2196,
    0x2197, 0x2198, 0x2199, 0x21A9, 0x21AA, 0x2328, 0x23CF, 0x24C2, 0x25AA,
    0x25AB, 0x25B6, 0x25C0, 0x25FB, 0x25FC, 0x2600, 0x2601, 0x2602, 0x2603,
    0x2604, 0x260E, 0x2611, 0x2618, 0x261D, 0x2620, 0x2622, 0x2623, 0x2626,
    0x262A, 0x262E, 0x262F, 0x2638, 0x2639, 0x263A, 0x2640, 0x2642, 0x2660,
    0x2663, 0x2665, 0x2666, 0x2668, 0x267B, 0x267E, 0x267F, 0x2692, 0x2693,
    0x2694, 0x2695, 0x2696, 0x2697, 0x2699, 0x269B, 0x269C, 0x26A0, 0x26A1,
    0x26A7, 0x26B0, 0x26B1, 0x26C8, 0x26CE, 0x26CF, 0x26D1, 0x26D3, 0x26D4,
    0x26E9, 0x26EA, 0x26F0, 0x26F1, 0x26F4, 0x26F5, 0x26F7, 0x26F8, 0x26F9,
    0x26FA, 0x26FD, 0x2702, 0x2705, 0x2708, 0x2709, 0x270C, 0x270D, 0x270F,
    0x2712, 0x2714, 0x2716, 0x271D, 0x2721, 0x2728, 0x2733, 0x2734, 0x2744,
    0x2747, 0x274C, 0x274E, 0x2757, 0x2763, 0x2764, 0x27A1, 0x27B0, 0x27BF,
    0x2934, 0x2935, 0x2B05, 0x2B06, 0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
    0x3030, 0x303D, 0x3297, 0x3299,
)


@dataclass(frozen=True)
class EmojiRecord:
    glyph: str
    name: str
    aliases: tuple[str, ...] = ()


def search_emojis(emojis: tuple[EmojiRecord, ...], query: str) -> tuple[EmojiRecord, ...]:
    needle = " ".join(query.casefold().replace("-", " ").split())
    if not needle:
        return emojis
    tokens = needle.split()
    matches: list[EmojiRecord] = []
    for emoji in emojis:
        haystack = " ".join((emoji.glyph, emoji.name, *emoji.aliases)).casefold().replace("-", " ")
        if needle in haystack or all(token in haystack for token in tokens):
            matches.append(emoji)
    return tuple(matches)


def load_emojis() -> tuple[EmojiRecord, ...]:
    """Load the local GTK emoji catalog, then fill gaps from Unicode names."""
    by_glyph: dict[str, EmojiRecord] = {}
    _merge(by_glyph, _load_gtk_file(GTK_EMOJI_LOCALE_DIR / "es.gresource", "es"))
    _merge(by_glyph, _load_gtk_locale("en"))
    _merge(by_glyph, _load_emoji_test_files())
    if len(by_glyph) < 200:
        _merge(by_glyph, _load_unicodedata_fallback())
    else:
        _enrich_unicodedata_aliases(by_glyph)
    return tuple(by_glyph.values())


def _merge(by_glyph: dict[str, EmojiRecord], incoming: tuple[EmojiRecord, ...]) -> None:
    for record in incoming:
        current = by_glyph.get(record.glyph)
        if current is None:
            by_glyph[record.glyph] = record
            continue
        aliases = list(current.aliases)
        seen = {current.name.casefold(), *(item.casefold() for item in aliases)}
        if record.name and record.name.casefold() not in seen:
            aliases.append(record.name)
            seen.add(record.name.casefold())
        for alias in record.aliases:
            if alias and alias.casefold() not in seen:
                aliases.append(alias)
                seen.add(alias.casefold())
        by_glyph[record.glyph] = EmojiRecord(glyph=record.glyph, name=current.name, aliases=tuple(aliases))


def _load_gtk_locale(locale: str) -> tuple[EmojiRecord, ...]:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib, Gtk  # noqa: F401
    except (ImportError, ValueError):
        return ()
    path = GTK_EMOJI_RESOURCE if locale == "en" else f"/org/gtk/libgtk/emoji/{locale}.data"
    try:
        payload = Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
    except Exception:
        return ()
    return _records_from_variant_bytes(payload)


def _load_gtk_file(path: Path, locale: str) -> tuple[EmojiRecord, ...]:
    if not path.is_file():
        return ()
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return ()
    try:
        resource = Gio.Resource.load(str(path))
        payload = resource.lookup_data(
            f"/org/gtk/libgtk/emoji/{locale}.data",
            Gio.ResourceLookupFlags.NONE,
        )
    except Exception:
        return ()
    return _records_from_variant_bytes(payload)


def _records_from_variant_bytes(payload) -> tuple[EmojiRecord, ...]:
    from gi.repository import GLib

    try:
        variant = GLib.Variant.new_from_bytes(
            GLib.VariantType.new(GTK_EMOJI_VARIANT),
            payload,
            True,
        )
    except Exception:
        return ()
    records: list[EmojiRecord] = []
    for index in range(variant.n_children()):
        codes, name, keyword = variant.get_child_value(index).unpack()
        glyph = "".join(chr(code) for code in codes if code)
        if not glyph:
            continue
        aliases = _aliases_for(glyph, name, keyword)
        records.append(EmojiRecord(glyph=glyph, name=str(name or glyph), aliases=aliases))
    return tuple(records)


def _load_emoji_test_files() -> tuple[EmojiRecord, ...]:
    for path in GTK_EMOJI_TEST_CANDIDATES:
        if path.is_file():
            return _parse_emoji_test(path)
    return ()


def _parse_emoji_test(path: Path) -> tuple[EmojiRecord, ...]:
    records: list[EmojiRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for line in lines:
        if "; fully-qualified" not in line or line.startswith("#"):
            continue
        left, _, right = line.partition("#")
        hex_part = left.split(";")[0]
        codes = []
        for token in hex_part.split():
            try:
                codes.append(int(token, 16))
            except ValueError:
                codes = []
                break
        if not codes:
            continue
        glyph = "".join(chr(code) for code in codes)
        comment = right.strip()
        name = comment.split(" ", 1)[-1].strip() if comment else glyph
        records.append(EmojiRecord(glyph=glyph, name=name or glyph, aliases=_aliases_for(glyph, name)))
    return tuple(records)


def _load_unicodedata_fallback() -> tuple[EmojiRecord, ...]:
    records: list[EmojiRecord] = []
    seen: set[str] = set()
    codepoints = list(_SINGLE_CODEPOINTS)
    for start, end in _EMOJI_RANGES:
        codepoints.extend(range(start, end + 1))
    for code in codepoints:
        glyph = chr(code)
        if glyph in seen:
            continue
        name = unicodedata.name(glyph, "")
        if not name:
            continue
        seen.add(glyph)
        records.append(EmojiRecord(glyph=glyph, name=name.casefold(), aliases=_aliases_for(glyph, name)))
    return tuple(records)


def _enrich_unicodedata_aliases(by_glyph: dict[str, EmojiRecord]) -> None:
    for glyph, record in list(by_glyph.items()):
        if len(glyph) != 1:
            continue
        unicode_name = unicodedata.name(glyph, "")
        if not unicode_name:
            continue
        aliases = list(record.aliases)
        seen = {record.name.casefold(), *(item.casefold() for item in aliases)}
        for alias in _aliases_for(glyph, unicode_name):
            if alias.casefold() not in seen:
                aliases.append(alias)
                seen.add(alias.casefold())
        by_glyph[glyph] = EmojiRecord(glyph=glyph, name=record.name, aliases=tuple(aliases))


def _aliases_for(glyph: str, *parts: str) -> tuple[str, ...]:
    aliases: list[str] = []
    seen: set[str] = set()
    if len(glyph) == 1:
        unicode_name = unicodedata.name(glyph, "")
        if unicode_name:
            parts = (*parts, unicode_name)
    for part in parts:
        cleaned = " ".join(str(part).replace("_", " ").replace("-", " ").split())
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered not in seen:
            aliases.append(lowered)
            seen.add(lowered)
        compact = lowered.replace(" ", "")
        if compact and compact not in seen:
            aliases.append(compact)
            seen.add(compact)
    return tuple(aliases)
