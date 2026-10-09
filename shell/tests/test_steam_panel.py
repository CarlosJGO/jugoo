from __future__ import annotations

from types import SimpleNamespace

import shell.ui.edge_slide as edge_slide
from shell.models import SteamCatalogSnapshot, SteamGame
from shell.ui.door import symmetric_ease
from shell.widgets.juegos.presentation import (
    cover_size,
    filter_games,
    grid_layout,
    grid_move,
    image_size,
    normalize_search_text,
    status_message,
    summary_text,
)


def _theme(duration: int, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(animation=SimpleNamespace(enabled=enabled, duration=duration))


def _game(appid: str, name: str, **extra) -> SteamGame:
    return SteamGame(appid=appid, name=name, library_path="/lib", **extra)


# —— Edge slide animation ——


def test_slide_duration_uses_task1_bounds() -> None:
    assert edge_slide.slide_duration_ms(_theme(160)) == 320
    assert edge_slide.slide_duration_ms(_theme(180)) == 360
    assert edge_slide.slide_duration_ms(_theme(60)) == edge_slide.SLIDE_MIN_DURATION_MS
    assert edge_slide.slide_duration_ms(_theme(1000)) == edge_slide.SLIDE_MAX_DURATION_MS
    assert edge_slide.slide_duration_ms(_theme(160, enabled=False)) == 0
    assert edge_slide.slide_duration_ms(_theme(0)) == 0


def test_slide_offset_starts_fully_off_screen_and_lands_at_zero() -> None:
    assert edge_slide.slide_offset(0.0, 460) == 460
    assert edge_slide.slide_offset(1.0, 460) == 0
    assert edge_slide.slide_offset(0.5, 460) == 230
    assert edge_slide.slide_offset(-1.0, 460) == 460
    assert edge_slide.slide_offset(2.0, 460) == 0


def test_reversed_slide_keeps_speed() -> None:
    assert edge_slide.travel_duration_ms(320, 0.0, 1.0) == 320
    assert edge_slide.travel_duration_ms(320, 0.25, 0.0) == 80
    assert edge_slide.travel_duration_ms(320, 0.75, 1.0) == 80
    assert edge_slide.travel_duration_ms(320, 1.0, 1.0) == 0


def test_slide_curve_is_symmetric_and_monotonic() -> None:
    samples = [symmetric_ease(index / 20) for index in range(21)]
    assert samples[0] == 0.0 and samples[-1] == 1.0
    assert all(left <= right for left, right in zip(samples, samples[1:]))
    assert abs(samples[4] + samples[16] - 1.0) < 1e-9


def test_edge_slide_snaps_when_animations_are_off() -> None:
    previous = edge_slide.active_theme
    edge_slide.active_theme = lambda: _theme(160, enabled=False)
    try:
        slide = edge_slide.EdgeSlide()
        frames: list[float] = []
        done: list[bool] = []
        slide.set_frame_callback(frames.append)
        slide.slide_in(on_complete=done.append)
        slide.slide_out(on_complete=done.append)
        assert frames == [1.0, 0.0]
        assert done == [True, False]
        assert not slide.animating
    finally:
        edge_slide.active_theme = previous


def test_edge_slide_reverses_from_current_progress() -> None:
    previous = edge_slide.active_theme
    edge_slide.active_theme = lambda: _theme(160)
    try:
        slide = edge_slide.EdgeSlide()
        slide.slide_in()
        assert slide.animating
        slide.apply(0.4)
        slide.slide_out()
        assert slide.animating
        assert slide._from == 0.4 and slide._to == 0.0
        assert slide._duration_ms == edge_slide.travel_duration_ms(320, 0.4, 0.0)
        slide.cancel()
    finally:
        edge_slide.active_theme = previous


# —— Panel text and geometry ——


def test_status_messages_cover_empty_states() -> None:
    assert status_message(SteamCatalogSnapshot()) == "Buscando juegos…"
    assert status_message(SteamCatalogSnapshot(scanned=True)) == "Steam no está instalado."
    assert (
        status_message(SteamCatalogSnapshot(scanned=True, steam_found=True, unavailable_libraries=("/mnt/x",)))
        == "Ninguna biblioteca de Steam está accesible."
    )
    assert (
        status_message(SteamCatalogSnapshot(scanned=True, steam_found=True, libraries=("/lib",)))
        == "No hay juegos instalados."
    )
    assert status_message(SteamCatalogSnapshot(games=(_game("1", "A"),))) == ""
    with_games = SteamCatalogSnapshot(games=(_game("1", "A"),), scanned=True)
    assert status_message(with_games, "zelda", 0) == "Sin resultados para «zelda»."
    assert status_message(with_games, "  ", 0) == ""
    assert status_message(with_games, "a", 1) == ""


def test_summary_counts_games_and_unmounted_libraries() -> None:
    assert summary_text(SteamCatalogSnapshot()) == ""
    games = (_game("1", "A"), _game("2", "B"))
    assert summary_text(SteamCatalogSnapshot(games=games, scanned=True)) == "2 juegos"
    assert (
        summary_text(SteamCatalogSnapshot(games=games[:1], scanned=True, unavailable_libraries=("/a", "/b")))
        == "1 juego · 2 bibliotecas sin montar"
    )


def test_cover_geometry_keeps_capsule_ratio() -> None:
    assert cover_size(124) == (124, 186)
    assert image_size("vertical", 124) == (124, 186)
    assert image_size("header", 124) == (124, 58)


def test_grid_layout_fits_columns_to_available_width() -> None:
    # Default 460 px panel leaves 424 px of grid; cards add 14 px of padding and border.
    columns, card = grid_layout(424, 112, 12, chrome=14)
    assert (columns, card) == (3, 119)
    assert columns * (card + 14) + (columns - 1) * 12 <= 424
    assert grid_layout(424, 124, 12, chrome=14) == (2, 192)
    assert grid_layout(900, 112, 12, chrome=14)[0] == 6
    assert grid_layout(100, 112, 12, chrome=14) == (1, 86)
    assert grid_layout(0, 112, 12)[1] == 48


def test_grid_move_follows_rows_and_stops_at_edges() -> None:
    # 3 columns, 8 items: rows [0 1 2] [3 4 5] [6 7]
    assert grid_move(0, 8, 3, dx=1) == 1
    assert grid_move(2, 8, 3, dx=1) == 3
    assert grid_move(0, 8, 3, dx=-1) == 0
    assert grid_move(7, 8, 3, dx=1) == 7
    assert grid_move(1, 8, 3, dy=1) == 4
    assert grid_move(4, 8, 3, dy=1) == 7
    assert grid_move(5, 8, 3, dy=1) == 7
    assert grid_move(6, 8, 3, dy=1) == 6
    assert grid_move(4, 8, 3, dy=-1) == 1
    assert grid_move(1, 8, 3, dy=-1) == 1
    assert grid_move(-1, 8, 3, dy=1) == 0
    assert grid_move(0, 0, 3, dy=1) == -1


def test_filter_games_matches_words_and_ranks_prefixes() -> None:
    games = (
        _game("1", "Age of Empires II: Definitive Edition"),
        _game("2", "Batman™: Arkham Origins"),
        _game("3", "Dishonored"),
        _game("4", "Pokémon Arena"),
        _game("5", "Super Meat Boy"),
    )
    assert filter_games(games, "") == games
    assert [g.appid for g in filter_games(games, "batman arkham")] == ["2"]
    assert [g.appid for g in filter_games(games, "empires age")] == ["1"]
    assert [g.appid for g in filter_games(games, "pokemon")] == ["4"]
    assert [g.appid for g in filter_games(games, "  DISHON ")] == ["3"]
    # Name prefix hits first, then the rest in catalog order.
    assert [g.appid for g in filter_games(games, "s")] == ["5", "1", "2", "3"]
    assert [g.appid for g in filter_games(games, "40800")] == []
    assert normalize_search_text("Batman™: Arkham-Origins") == "batman arkham origins"


# —— GTK panel (constructed, never mapped) ——


def test_panel_builds_cards_with_placeholder_and_spinner() -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")
    from shell.widgets.juegos.steam_panel import SteamGamesPanel

    activated: list[str] = []
    panel = SteamGamesPanel(Gtk.Window(), on_activate=lambda game: activated.append(game.appid))
    try:
        # Non-resizable kept the layer at 200x1 instead of fullscreen.
        assert panel.get_resizable()
        panel.set_snapshot(SteamCatalogSnapshot())
        assert panel._status.get_text() == "Buscando juegos…"

        games = (
            _game("312520", "Rain World", image_pending=True),
            _game("40800", "Super Meat Boy"),
        )
        panel.set_snapshot(SteamCatalogSnapshot(games=games, scanned=True, libraries=("/lib",)))
        assert panel._status.get_text() == ""
        assert panel._summary.get_text() == "2 juegos"
        card = panel._cards["312520"]
        assert card._initials.get_text() == "RW"
        assert card._spinner.get_property("active")
        first_card = card

        panel.update_game(_game("312520", "Rain World"))
        assert not card._spinner.get_property("active")

        panel.set_snapshot(SteamCatalogSnapshot(games=games, scanned=True, libraries=("/lib",)))
        assert panel._cards["312520"] is first_card

        assert not panel.is_effectively_open()
    finally:
        panel.destroy()


def test_panel_search_navigation_activation_and_layout() -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")
    from shell.widgets.juegos.steam_panel import SteamGamesPanel

    activated: list[str] = []
    panel = SteamGamesPanel(Gtk.Window(), on_activate=lambda game: activated.append(game.appid))
    names = ("Age of Empires II", "Batman", "Biped", "Crashlands 2", "Dishonored", "Duck Game", "Grow Home")
    games = tuple(_game(str(index + 1), name) for index, name in enumerate(names))
    try:
        panel.set_snapshot(SteamCatalogSnapshot(games=games, scanned=True, libraries=("/lib",)))
        assert panel.selected_game.appid == "1"
        # The entry must be shown too, or typing hits an unrealized widget.
        assert panel._search_row.get_visible() and panel._search.get_visible()

        panel.layout_for_width(428)
        assert panel.columns == 3
        assert all(card.card_width == panel._card_width for card in panel._cards.values())

        def press(keyval: int) -> None:
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval = keyval
            panel._on_key_press(panel, event)

        press(Gdk.KEY_Right)
        assert panel.selected_game.appid == "2"
        press(Gdk.KEY_Down)
        assert panel.selected_game.appid == "5"
        press(Gdk.KEY_Down)
        assert panel.selected_game.appid == "7"
        press(Gdk.KEY_Return)
        assert activated == ["7"]

        panel._search.set_text("b")
        panel._apply_filter(keep_selection=False)
        assert [game.name for game in panel.visible_games] == ["Batman", "Biped"]
        assert panel.selected_game.name == "Batman"
        assert not panel._cards["1"].get_visible()

        panel._search.set_text("zelda")
        panel._apply_filter(keep_selection=False)
        assert panel.visible_games == ()
        assert panel.selected_game is None
        assert panel._status.get_text() == "Sin resultados para «zelda»."
        press(Gdk.KEY_Return)
        assert activated == ["7"]

        panel._reset_search()
        assert len(panel.visible_games) == len(games)
        assert panel._status.get_text() == ""

        panel.select_index(4, reveal=False)
        panel.set_snapshot(SteamCatalogSnapshot(games=games, scanned=True, libraries=("/lib",)))
        assert panel.selected_game.appid == "5"

        panel._on_card_hover(panel._cards["3"])
        assert panel.selected_game.appid == "3"

        assert not panel.is_effectively_open()
        panel.close_panel()
        assert not panel.get_visible()
    finally:
        panel.destroy()


def _run() -> None:
    import inspect

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        assert not inspect.signature(test).parameters
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
