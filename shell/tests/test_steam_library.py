from __future__ import annotations

import json
from pathlib import Path

from shell.eventbus import EventBus
from shell.models import SteamCatalogSnapshot, normalize_steam_appid
from shell.servicios.steam.library import (
    STATE_DOWNLOADING,
    STATE_FULLY_INSTALLED,
    STATE_UPDATE_REQUIRED,
    STATE_UPDATE_RUNNING,
    discover_steam_libraries,
    is_fully_installed,
    is_non_game,
    parse_library_paths,
    read_app_manifest,
    read_library_folders,
    scan_steam_games,
    steam_root_candidates,
)
from shell.servicios.steam.service import (
    STEAM_GAME_IGNORE_TOGGLE_REQUESTED,
    STEAM_GAMES_CHANGED,
    STEAM_SHOW_IGNORED_TOGGLE_REQUESTED,
    SteamGamesService,
)
from shell.servicios.steam.images import (
    ARTWORK_HEADER,
    ARTWORK_VERTICAL,
    SOURCE_CACHE,
    SOURCE_NETWORK,
    SOURCE_STEAM,
    SteamArtworkCache,
    cached_image_path,
    find_librarycache_image,
    is_image_data,
    placeholder_initials,
)
from shell.servicios.steam.service import STEAM_GAME_ARTWORK_CHANGED
from shell.servicios.steam.store import load_ignored_appids, save_ignored_appids
from shell.servicios.steam.vdf import VdfError, parse_vdf, vdf_get

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 16


def _offline_artwork(tmp_path: Path, root: Path) -> SteamArtworkCache:
    return SteamArtworkCache(roots=(root,), cache_dir=tmp_path / "art-cache", network_enabled=lambda: False)


def _cover(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JPEG)
    return path


def _manifest(appid: str, name: str, flags: int = 4, installdir: str = "") -> str:
    lines = ['"AppState"', "{"]
    if appid:
        lines.append(f'\t"appid"\t\t"{appid}"')
    if name:
        lines.append(f'\t"name"\t\t"{name}"')
    if flags >= 0:
        lines.append(f'\t"StateFlags"\t\t"{flags}"')
    lines.append(f'\t"installdir"\t\t"{installdir or name}"')
    lines.append('\t"UserConfig"\n\t{\n\t}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _library(root: Path, manifests: dict[str, str]) -> Path:
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    for appid, text in manifests.items():
        (steamapps / f"appmanifest_{appid}.acf").write_text(text, encoding="utf-8")
    return root


def _library_folders(root: Path, paths: list[Path]) -> None:
    entries = "\n".join(
        f'\t"{index}"\n\t{{\n\t\t"path"\t\t"{path}"\n\t\t"apps"\n\t\t{{\n\t\t}}\n\t}}'
        for index, path in enumerate(paths)
    )
    text = f'"libraryfolders"\n{{\n\t"contentstatsid"\t\t"123"\n{entries}\n}}\n'
    (root / "steamapps" / "libraryfolders.vdf").write_text(text, encoding="utf-8")


# —— VDF parser ——


def test_parse_vdf_nested_sections_and_escapes() -> None:
    data = parse_vdf(
        '// comment\n"root"\n{\n\t"path"\t"C:\\\\Games\\\\Steam"\n'
        '\t"quote"\t"say \\"hi\\""\n\t"child" { "a" "1" }\n\tbare value\n}\n'
    )
    root = data["root"]
    assert root["path"] == "C:\\Games\\Steam"
    assert root["quote"] == 'say "hi"'
    assert root["child"] == {"a": "1"}
    assert root["bare"] == "value"


def test_parse_vdf_skips_platform_conditionals() -> None:
    data = parse_vdf('"root" { "key" "value" [$WIN32] "other" "2" }')
    assert data["root"] == {"key": "value", "other": "2"}


def test_parse_vdf_rejects_malformed_documents() -> None:
    for text in ('"root" { "a" "1"', '"root" { "a" "1" } }', '"root" { "a" }', '"root" { "a" "unterminated'):
        try:
            parse_vdf(text)
        except VdfError:
            continue
        raise AssertionError(f"accepted malformed VDF: {text!r}")


def test_vdf_get_is_case_insensitive() -> None:
    assert vdf_get({"AppState": {"StateFlags": "4"}}, "appstate") == {"StateFlags": "4"}
    assert vdf_get("not a dict", "key") is None


# —— Manifests ——


def test_read_app_manifest_valid_game(tmp_path: Path) -> None:
    library = _library(tmp_path / "lib", {"312520": _manifest("312520", "Rain World")})
    game = read_app_manifest(library / "steamapps" / "appmanifest_312520.acf", library)
    assert game is not None
    assert (game.appid, game.name, game.library_path, game.install_dir) == (
        "312520",
        "Rain World",
        str(library),
        "Rain World",
    )


def test_read_app_manifest_rejects_invalid_and_incomplete(tmp_path: Path) -> None:
    library = _library(
        tmp_path / "lib",
        {
            "1": '"AppState" { "appid" "1" ',
            "2": _manifest("", "No appid"),
            "3": _manifest("3", "No flags", flags=-1),
            "4": _manifest("abc", "Bad appid"),
            "5": '"Other" { "appid" "5" }',
        },
    )
    for appid in ("1", "2", "3", "4", "5"):
        assert read_app_manifest(library / "steamapps" / f"appmanifest_{appid}.acf", library) is None


def test_read_app_manifest_falls_back_to_installdir_for_name(tmp_path: Path) -> None:
    library = _library(tmp_path / "lib", {"7": _manifest("7", "", installdir="SomeGame")})
    game = read_app_manifest(library / "steamapps" / "appmanifest_7.acf", library)
    assert game is not None and game.name == "SomeGame"


def test_state_flags_only_accept_complete_installs() -> None:
    assert is_fully_installed(STATE_FULLY_INSTALLED)
    assert is_fully_installed(STATE_FULLY_INSTALLED | STATE_UPDATE_REQUIRED)
    assert not is_fully_installed(0)
    assert not is_fully_installed(1026)  # first install downloading
    assert not is_fully_installed(STATE_FULLY_INSTALLED | STATE_UPDATE_RUNNING)
    assert not is_fully_installed(STATE_FULLY_INSTALLED | STATE_DOWNLOADING)


def test_non_game_entries_are_excluded() -> None:
    for appid, name in (
        ("1070560", "Steam Linux Runtime 1.0 (scout)"),
        ("999001", "Steam Linux Runtime 9.0"),
        ("999002", "Proton 9.0"),
        ("999003", "Proton Experimental"),
        ("999004", "Steamworks Common Redistributables"),
    ):
        assert is_non_game(appid, name), name
    assert not is_non_game("312520", "Rain World")
    assert not is_non_game("999005", "Protonwar")


# —— Discovery ——


def test_library_folders_reads_paths_and_skips_metadata(tmp_path: Path) -> None:
    root = _library(tmp_path / "Steam", {})
    _library_folders(root, [root, tmp_path / "extra"])
    assert read_library_folders(root / "steamapps" / "libraryfolders.vdf") == (root, tmp_path / "extra")


def test_library_folders_old_format(tmp_path: Path) -> None:
    path = tmp_path / "libraryfolders.vdf"
    path.write_text('"LibraryFolders" { "TimeNextStatsReport" "1" "1" "/games/steam" }', encoding="utf-8")
    assert read_library_folders(path) == (Path("/games/steam"),)


def test_discovery_follows_vdf_and_reports_unmounted(tmp_path: Path) -> None:
    root = _library(tmp_path / "Steam", {})
    mounted = _library(tmp_path / "mnt" / "Almighty", {})
    unmounted = tmp_path / "mnt" / "MiniMighty"
    _library_folders(root, [root, mounted, unmounted])
    alias = tmp_path / "steam-link"
    alias.symlink_to(root)
    discovery = discover_steam_libraries((root, alias, tmp_path / "flatpak"), ())
    assert discovery.steam_found
    assert discovery.libraries == (root, mounted)
    assert discovery.unavailable == (unmounted,)


def test_discovery_uses_extra_paths_when_vdf_is_missing(tmp_path: Path) -> None:
    root = _library(tmp_path / "Steam", {})
    extra = _library(tmp_path / "Fear", {})
    discovery = discover_steam_libraries((root,), (extra / "steamapps",))
    assert discovery.libraries == (root, extra)


def test_discovery_without_steam(tmp_path: Path) -> None:
    discovery = discover_steam_libraries((tmp_path / "nothing",), ())
    assert discovery == discovery.__class__((), (), False)


def test_parse_library_paths_splits_and_expands() -> None:
    home = Path.home()
    assert parse_library_paths(" /mnt/a ; ~/games:/mnt/b\n") == (Path("/mnt/a"), home / "games", Path("/mnt/b"))
    assert parse_library_paths("") == ()
    assert parse_library_paths(None) == ()


def test_root_candidates_include_native_symlink_and_flatpak(tmp_path: Path) -> None:
    candidates = steam_root_candidates(tmp_path)
    assert tmp_path / ".local/share/Steam" in candidates
    assert tmp_path / ".steam/steam" in candidates
    assert tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam" in candidates


# —— Full scan ——


def test_scan_dedupes_filters_and_sorts(tmp_path: Path) -> None:
    root = _library(
        tmp_path / "Steam",
        {
            "813780": _manifest("813780", "Age of Empires II: Definitive Edition"),
            "1493710": _manifest("1493710", "Proton Experimental"),
            "228980": _manifest("228980", "Steamworks Common Redistributables"),
            "500": _manifest("500", "downloading", flags=1026),
            "broken": "not a manifest {",
        },
    )
    second = _library(
        tmp_path / "Almighty",
        {
            "813780": _manifest("813780", "Age of Empires II (copy)"),
            "312520": _manifest("312520", "Rain World"),
            "40800": _manifest("40800", "brütal legend"),
        },
    )
    _library_folders(root, [root, second, tmp_path / "unmounted"])
    result = scan_steam_games((root,), ())
    assert [game.name for game in result.games] == [
        "Age of Empires II: Definitive Edition",
        "brütal legend",
        "Rain World",
    ]
    assert result.games[0].library_path == str(root)
    assert result.unavailable == (tmp_path / "unmounted",)


# —— Persistence & service ——


def test_ignored_appids_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "steam-prefs.json"
    save_ignored_appids(path, ["312520", "abc", "0312520", "40800"])
    assert load_ignored_appids(path) == ("312520", "40800")
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    path.write_text("{broken", encoding="utf-8")
    assert load_ignored_appids(path) == ()


def test_normalize_steam_appid() -> None:
    assert normalize_steam_appid(" 0312520 ") == "312520"
    assert normalize_steam_appid(40800) == "40800"
    assert normalize_steam_appid("0") == ""
    assert normalize_steam_appid("12a") == ""
    assert normalize_steam_appid(None) == ""


def test_service_ignore_persists_and_recovers(tmp_path: Path) -> None:
    root = _library(
        tmp_path / "Steam",
        {"312520": _manifest("312520", "Rain World"), "40800": _manifest("40800", "Brutal Legend")},
    )
    prefs = tmp_path / "prefs.json"
    bus = EventBus()
    emitted: list[SteamCatalogSnapshot] = []
    bus.subscribe(STEAM_GAMES_CHANGED, emitted.append)
    service = SteamGamesService(
        bus, prefs_path=prefs, roots=(root,), extra_paths=lambda: (), artwork=_offline_artwork(tmp_path, root)
    )
    service.start()
    assert not service.snapshot.scanned
    service.refresh()
    assert [game.appid for game in service.snapshot.games] == ["40800", "312520"]

    bus.emit(STEAM_GAME_IGNORE_TOGGLE_REQUESTED, "312520")
    assert [game.appid for game in service.snapshot.games] == ["40800"]
    assert emitted[-1].ignored_appids == ("312520",)

    bus.emit(STEAM_SHOW_IGNORED_TOGGLE_REQUESTED, True)
    assert service.snapshot.game_by_appid("312520") is not None
    assert service.snapshot.is_ignored("312520")
    service.close()

    restarted = SteamGamesService(
        EventBus(), prefs_path=prefs, roots=(root,), extra_paths=lambda: (), artwork=_offline_artwork(tmp_path, root)
    )
    restarted.start()
    restarted.refresh()
    assert [game.appid for game in restarted.snapshot.games] == ["40800"]
    restarted.unignore("312520")
    assert [game.appid for game in restarted.snapshot.games] == ["40800", "312520"]
    assert load_ignored_appids(prefs) == ()
    restarted.close()


def test_service_merges_ignored_appids_from_settings(tmp_path: Path) -> None:
    from shell.servicios.steam.service import parse_appid_list

    assert parse_appid_list("431960; 312520,  40800 abc 0 431960") == ("431960", "312520", "40800")
    assert parse_appid_list("") == ()

    root = _library(
        tmp_path / "Steam",
        {"312520": _manifest("312520", "Rain World"), "40800": _manifest("40800", "Brutal Legend")},
    )
    prefs = tmp_path / "prefs.json"
    setting = {"ignored": "40800", "show": False}
    service = SteamGamesService(
        EventBus(),
        prefs_path=prefs,
        roots=(root,),
        extra_paths=lambda: (),
        artwork=_offline_artwork(tmp_path, root),
        configured_ignored=lambda: parse_appid_list(setting["ignored"]),
        configured_show_ignored=lambda: setting["show"],
    )
    service.start()
    service.refresh()
    assert [game.appid for game in service.snapshot.games] == ["312520"]
    assert service.snapshot.is_ignored("40800")

    service.ignore("312520")
    assert service.snapshot.games == ()
    # Only the prefs-file entry is persisted; the setting stays the setting's.
    assert load_ignored_appids(prefs) == ("312520",)

    service.unignore("40800")
    assert service.snapshot.is_ignored("40800")

    setting["show"] = True
    service.refresh()
    assert {game.appid for game in service.snapshot.games} == {"312520", "40800"}
    assert service.snapshot.include_ignored

    setting.update(ignored="", show=False)
    service.refresh()
    assert [game.appid for game in service.snapshot.games] == ["40800"]
    service.close()


def test_service_refresh_async_runs_off_thread_and_coalesces(tmp_path: Path) -> None:
    import threading

    root = _library(tmp_path / "Steam", {"312520": _manifest("312520", "Rain World")})
    release = threading.Event()
    calls: list[str] = []

    def scanner(roots, extra):
        calls.append(threading.current_thread().name)
        release.wait(2)
        return scan_steam_games(roots, extra)

    bus = EventBus()
    done = threading.Event()
    bus.subscribe(STEAM_GAMES_CHANGED, lambda _snap: done.set() if len(calls) >= 2 else None)
    service = SteamGamesService(
        bus,
        prefs_path=tmp_path / "p.json",
        roots=(root,),
        extra_paths=lambda: (),
        scanner=scanner,
        artwork=_offline_artwork(tmp_path, root),
    )
    service.refresh_async()
    service.refresh_async()
    service.refresh_async()
    release.set()
    assert done.wait(3)
    assert len(calls) == 2
    assert all(name == "jugoo-steam-scan" for name in calls)
    assert service.snapshot.games[0].appid == "312520"
    service.close()


# —— Artwork ——


def test_librarycache_lookup_covers_all_layouts(tmp_path: Path) -> None:
    cache = tmp_path / "Steam" / "appcache" / "librarycache"
    old = _cover(cache / "312520" / "library_600x900.jpg")
    hashed = _cover(cache / "4450800" / "9dcf" / "library_capsule.jpg")
    flat = _cover(cache / "40800_library_600x900.jpg")
    header = _cover(cache / "105600" / "aa62" / "library_header.jpg")
    assert find_librarycache_image("312520", ARTWORK_VERTICAL, (cache,)) == old
    assert find_librarycache_image("4450800", ARTWORK_VERTICAL, (cache,)) == hashed
    assert find_librarycache_image("40800", ARTWORK_VERTICAL, (cache,)) == flat
    assert find_librarycache_image("105600", ARTWORK_VERTICAL, (cache,)) is None
    assert find_librarycache_image("105600", ARTWORK_HEADER, (cache,)) == header


def test_resolution_order_steam_then_cache_then_header(tmp_path: Path) -> None:
    root = tmp_path / "Steam"
    librarycache = root / "appcache" / "librarycache"
    jugoo_cache = tmp_path / "jugoo"
    fetched: list[str] = []
    artwork = SteamArtworkCache(
        roots=(root,),
        cache_dir=jugoo_cache,
        fetcher=lambda url, _timeout: fetched.append(url),
        submit=lambda job: job(),
        network_enabled=lambda: False,
    )
    _cover(cached_image_path(jugoo_cache, "1", ARTWORK_VERTICAL))
    _cover(librarycache / "1" / "library_600x900.jpg")
    first = artwork.resolve("1", lambda *_: None).artwork
    assert first is not None and (first.source, first.kind) == (SOURCE_STEAM, ARTWORK_VERTICAL)

    _cover(cached_image_path(jugoo_cache, "2", ARTWORK_VERTICAL))
    _cover(librarycache / "2" / "header.jpg")
    second = artwork.resolve("2", lambda *_: None).artwork
    assert second is not None and (second.source, second.kind) == (SOURCE_CACHE, ARTWORK_VERTICAL)

    _cover(librarycache / "3" / "header.jpg")
    third = artwork.resolve("3", lambda *_: None)
    assert third.artwork is not None and third.artwork.kind == ARTWORK_HEADER
    assert not third.pending

    assert artwork.resolve("4", lambda *_: None) == third.__class__(None, False)
    assert fetched == []


def test_download_caches_vertical_and_is_not_repeated(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetcher(url: str, _timeout: float) -> bytes | None:
        fetched.append(url)
        return _JPEG if "library_600x900" in url else None

    ready: list[tuple[str, object]] = []
    artwork = SteamArtworkCache(
        roots=(tmp_path / "Steam",),
        cache_dir=tmp_path / "jugoo",
        fetcher=fetcher,
        submit=lambda job: job(),
        network_enabled=lambda: True,
    )
    resolution = artwork.resolve("312520", lambda appid, art: ready.append((appid, art)))
    assert resolution.pending
    appid, art = ready[0]
    assert appid == "312520" and art is not None
    assert (art.kind, art.source) == (ARTWORK_VERTICAL, SOURCE_NETWORK)
    assert Path(art.path).read_bytes() == _JPEG
    assert len(fetched) == 1

    again = artwork.resolve("312520", lambda *_: None)
    assert again.artwork is not None and again.artwork.source == SOURCE_CACHE
    assert not again.pending and len(fetched) == 1


def test_download_falls_back_to_header_and_remembers_misses(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetcher(url: str, _timeout: float) -> bytes | None:
        fetched.append(url)
        return _JPEG if url.endswith("/header.jpg") else None

    ready: list[object] = []
    artwork = SteamArtworkCache(
        roots=(tmp_path / "Steam",),
        cache_dir=tmp_path / "jugoo",
        fetcher=fetcher,
        submit=lambda job: job(),
        network_enabled=lambda: True,
    )
    artwork.resolve("10", lambda _appid, art: ready.append(art))
    assert ready[0] is not None and ready[0].kind == ARTWORK_HEADER

    # Local header present and the CDN has no vertical: keep the header, stop retrying.
    _cover(tmp_path / "Steam" / "appcache" / "librarycache" / "20" / "header.jpg")
    fetched.clear()
    ready.clear()
    first = artwork.resolve("20", lambda _appid, art: ready.append(art))
    assert first.pending and first.artwork is not None and first.artwork.kind == ARTWORK_HEADER
    assert ready == [None]
    assert all("library_600x900" in url for url in fetched)
    second = artwork.resolve("20", lambda _appid, art: ready.append(art))
    assert not second.pending and second.artwork is not None


def test_resolve_never_blocks_on_slow_network(tmp_path: Path) -> None:
    import threading
    import time

    release = threading.Event()
    done = threading.Event()

    def fetcher(_url: str, _timeout: float) -> bytes | None:
        release.wait(5)
        return None

    artwork = SteamArtworkCache(
        roots=(tmp_path / "Steam",),
        cache_dir=tmp_path / "jugoo",
        fetcher=fetcher,
        network_enabled=lambda: True,
    )
    started = time.monotonic()
    resolution = artwork.resolve("312520", lambda *_: done.set())
    assert time.monotonic() - started < 0.5
    assert resolution.pending and resolution.artwork is None
    release.set()
    assert done.wait(3)
    artwork.close()


def test_service_swaps_cover_when_download_finishes(tmp_path: Path) -> None:
    root = _library(tmp_path / "Steam", {"312520": _manifest("312520", "Rain World")})
    jobs: list = []
    artwork = SteamArtworkCache(
        roots=(root,),
        cache_dir=tmp_path / "jugoo",
        fetcher=lambda _url, _timeout: _JPEG,
        submit=jobs.append,
        network_enabled=lambda: True,
    )
    bus = EventBus()
    changed: list = []
    bus.subscribe(STEAM_GAME_ARTWORK_CHANGED, changed.append)
    service = SteamGamesService(
        bus, prefs_path=tmp_path / "p.json", roots=(root,), extra_paths=lambda: (), artwork=artwork
    )
    service.refresh()
    game = service.snapshot.games[0]
    assert game.image_pending and game.image_path == ""
    jobs.pop()()
    game = service.snapshot.games[0]
    assert not game.image_pending and game.image_kind == ARTWORK_VERTICAL
    assert Path(game.image_path).is_file()
    assert changed == [game]
    service.close()


def test_service_keeps_download_that_lands_during_rescan(tmp_path: Path) -> None:
    root = _library(tmp_path / "Steam", {"312520": _manifest("312520", "Rain World")})
    artwork = SteamArtworkCache(
        roots=(root,),
        cache_dir=tmp_path / "jugoo",
        fetcher=lambda _url, _timeout: _JPEG,
        submit=lambda job: job(),
        network_enabled=lambda: True,
    )
    service = SteamGamesService(
        EventBus(), prefs_path=tmp_path / "p.json", roots=(root,), extra_paths=lambda: (), artwork=artwork
    )
    service.refresh()
    game = service.snapshot.games[0]
    assert not game.image_pending and game.image_kind == ARTWORK_VERTICAL
    service.close()


def test_placeholder_initials_and_image_signatures() -> None:
    assert placeholder_initials("Rain World") == "RW"
    assert placeholder_initials("tModLoader") == "T"
    assert placeholder_initials("Batman™: Arkham Origins") == "BA"
    assert placeholder_initials("ΔV: Rings of Saturn Demo") == "ΔR"
    assert placeholder_initials("  ") == "?"
    assert is_image_data(_JPEG)
    assert is_image_data(b"\x89PNG\r\n\x1a\n....")
    assert not is_image_data(b"<html>404</html>")


def test_settings_catalog_exposes_steam_settings() -> None:
    from shell.settings.schema import CategoryId, settings_by_key

    catalog = settings_by_key()
    paths = catalog["juegos.steam_extra_library_paths"]
    assert paths.category is CategoryId.JUEGOS
    assert paths.config_attr == "STEAM_EXTRA_LIBRARY_PATHS"
    download = catalog["juegos.steam_artwork_download"]
    assert download.value_type == "bool"
    assert download.config_attr == "STEAM_ARTWORK_DOWNLOAD_ENABLED"
    assert catalog["juegos.steam_panel_width"].config_attr == "STEAM_PANEL_WIDTH"
    ignored = catalog["juegos.steam_ignored_appids"]
    assert ignored.value_type == "string" and ignored.config_attr == "STEAM_IGNORED_APPIDS"
    show = catalog["juegos.steam_show_ignored"]
    assert show.value_type == "bool" and show.config_attr == "STEAM_SHOW_IGNORED"


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
