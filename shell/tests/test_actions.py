"""Unit tests for the external action catalog (no GTK required)."""

from __future__ import annotations

from shell.actions import (
    UNSUPPORTED,
    dispatch_action,
    format_actions_help,
    known_action_names,
    resolve_actions_from_argv,
)


def test_resolve_preferred_action_form() -> None:
    assert resolve_actions_from_argv(["action", "launcher"]) == ("launcher",)
    assert resolve_actions_from_argv(["action", "launcher", "clipboard"]) == (
        "launcher",
        "clipboard",
    )


def test_resolve_legacy_flags() -> None:
    assert resolve_actions_from_argv(["--toggle-launcher"]) == ("launcher",)
    assert resolve_actions_from_argv(["--toggle-control-center"]) == ("control-center",)
    assert resolve_actions_from_argv(["--toggle-notifications"]) == ("notifications",)
    assert resolve_actions_from_argv(["--toggle-session"]) == ("session",)
    assert resolve_actions_from_argv(["--open-tasks"]) == ("tasks",)


def test_resolve_music_action() -> None:
    assert resolve_actions_from_argv(["action", "playStopMusic"]) == ("playStopMusic",)
    assert resolve_actions_from_argv(["action", "media"]) == ("media",)
    assert resolve_actions_from_argv(["--toggle-media"]) == ("media",)


def test_dispatch_music_action_uses_strawberry_transport() -> None:
    calls: list[str] = []

    class MediaService:
        def play_pause_player(self) -> None:
            calls.append("play_pause_player")

        def volume_up_player(self) -> None:
            calls.append("volume_up_player")

        def volume_down_player(self) -> None:
            calls.append("volume_down_player")

    class Shell:
        media_service = MediaService()

        def __getattr__(self, name: str):
            return lambda: calls.append(name)

    assert dispatch_action("playStopMusic", Shell()) is None
    assert calls == ["play_pause_player"]
    calls.clear()
    assert dispatch_action("musicVolumeUp", Shell()) is None
    assert dispatch_action("musicVolumeDown", Shell()) is None
    assert calls == ["volume_up_player", "volume_down_player"]


def test_resolve_music_volume_actions() -> None:
    assert resolve_actions_from_argv(["action", "musicVolumeUp"]) == ("musicVolumeUp",)
    assert resolve_actions_from_argv(["action", "musicVolumeDown"]) == (
        "musicVolumeDown",
    )


def test_dispatch_media_action_toggles_popup() -> None:
    calls: list[str] = []

    class Shell:
        def toggle_media_popup(self) -> None:
            calls.append("toggle_media_popup")

    assert dispatch_action("media", Shell()) is None
    assert calls == ["toggle_media_popup"]


def test_resolve_dedupes_and_preserves_order() -> None:
    assert resolve_actions_from_argv(
        ["--toggle-launcher", "action", "launcher", "emoji"]
    ) == ("launcher", "emoji")


def test_window_switcher_is_unsupported_token() -> None:
    assert resolve_actions_from_argv(["--toggle-window-switcher"]) == ("window-switcher",)
    assert "window-switcher" in UNSUPPORTED


def test_help_lists_core_actions() -> None:
    text = format_actions_help()
    for name in known_action_names():
        assert name in text
    assert "window-switcher" in text


if __name__ == "__main__":
    test_resolve_preferred_action_form()
    test_resolve_legacy_flags()
    test_resolve_music_action()
    test_dispatch_music_action_uses_strawberry_transport()
    test_resolve_music_volume_actions()
    test_dispatch_media_action_toggles_popup()
    test_resolve_dedupes_and_preserves_order()
    test_window_switcher_is_unsupported_token()
    test_help_lists_core_actions()
    print("ok")
