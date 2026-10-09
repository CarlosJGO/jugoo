"""Text and geometry for the games panel, kept free of GTK so it can be tested."""

from __future__ import annotations

from collections.abc import Sequence
import unicodedata

from ...models import SteamCatalogSnapshot, SteamGame

COVER_RATIO = 1.5  # Steam capsules are 600x900.
HEADER_RATIO = 215 / 460  # Steam header.jpg is 460x215.
MIN_CARD_WIDTH = 48

_SEPARATORS = str.maketrans({"-": " ", "_": " ", ":": " ", ".": " ", "™": "", "®": "", "©": ""})


def cover_size(card_width: int) -> tuple[int, int]:
    width = max(MIN_CARD_WIDTH, int(card_width))
    return width, int(round(width * COVER_RATIO))


def image_size(kind: str, card_width: int) -> tuple[int, int]:
    """Pixel size to decode an image at: a full capsule, or a header band across it."""
    width, height = cover_size(card_width)
    if kind == "header":
        return width, max(1, int(round(width * HEADER_RATIO)))
    return width, height


def grid_layout(available: int, target_card: int, spacing: int, chrome: int = 0) -> tuple[int, int]:
    """Columns that fit cards of at least ``target_card`` px, and the card width filling the row.

    ``chrome`` is the per-card padding around the cover, ``spacing`` the gap between columns.
    """
    available = max(0, int(available))
    spacing = max(0, int(spacing))
    chrome = max(0, int(chrome))
    target = max(MIN_CARD_WIDTH, int(target_card))
    columns = max(1, (available + spacing) // (target + chrome + spacing))
    card = (available - (columns - 1) * spacing) // columns - chrome
    return columns, max(MIN_CARD_WIDTH, card)


def grid_move(index: int, count: int, columns: int, dx: int = 0, dy: int = 0) -> int:
    """Index after an arrow key in a row-major grid; stays put at the edges.

    Down from a row above a shorter last row lands on the last item.
    """
    if count <= 0:
        return -1
    if not 0 <= index < count:
        return 0
    columns = max(1, int(columns))
    if dx:
        target = index + dx
        return target if 0 <= target < count else index
    if dy:
        target = index + dy * columns
        if 0 <= target < count:
            return target
        if dy > 0 and index // columns < (count - 1) // columns:
            return count - 1
    return index


def normalize_search_text(text: str) -> str:
    """Casefolded, accent-free, single-spaced text with title punctuation removed."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().translate(_SEPARATORS))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.split())


def filter_games(games: Sequence[SteamGame], query: str) -> tuple[SteamGame, ...]:
    """Launcher-style filter: whole query or every word must match; name prefixes first.

    Within each rank the catalog order (alphabetical) is kept.
    """
    needle = normalize_search_text(query)
    if not needle:
        return tuple(games)
    tokens = needle.split()
    prefix: list[SteamGame] = []
    rest: list[SteamGame] = []
    for game in games:
        name = normalize_search_text(game.name)
        haystack = f"{name} {game.appid}"
        if needle not in haystack and not all(token in haystack for token in tokens):
            continue
        (prefix if name.startswith(needle) else rest).append(game)
    return tuple(prefix + rest)


def status_message(snapshot: SteamCatalogSnapshot, query: str = "", matches: int | None = None) -> str:
    """Message shown instead of the grid; empty when there are games to show."""
    if snapshot.games:
        if query.strip() and matches == 0:
            return f"Sin resultados para «{query.strip()}»."
        return ""
    if not snapshot.scanned:
        return "Buscando juegos…"
    if not snapshot.steam_found and not snapshot.libraries:
        return "Steam no está instalado."
    if not snapshot.libraries:
        return "Ninguna biblioteca de Steam está accesible."
    return "No hay juegos instalados."


def summary_text(snapshot: SteamCatalogSnapshot) -> str:
    if not snapshot.scanned and not snapshot.games:
        return ""
    count = len(snapshot.games)
    parts = ["1 juego" if count == 1 else f"{count} juegos"]
    missing = len(snapshot.unavailable_libraries)
    if missing:
        parts.append("1 biblioteca sin montar" if missing == 1 else f"{missing} bibliotecas sin montar")
    return " · ".join(parts)
