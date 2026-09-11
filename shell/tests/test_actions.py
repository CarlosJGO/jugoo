"""Unit tests for the external action catalog (no GTK required)."""

from __future__ import annotations

from shell.actions import (
    UNSUPPORTED,
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
    test_resolve_dedupes_and_preserves_order()
    test_window_switcher_is_unsupported_token()
    test_help_lists_core_actions()
    print("ok")
