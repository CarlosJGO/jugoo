"""User application shortcut store, protection, and Hyprland sync."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shell.servicios.atajos.hypr_sync import (
    MARKER_BEGIN,
    MARKER_END,
    render_managed_block,
    sync_user_app_shortcuts_to_hypr,
)
from shell.servicios.atajos.manager import (
    UserAppShortcutError,
    add_user_app_shortcut,
    remove_user_app_shortcut,
    update_user_app_shortcut,
)
from shell.servicios.atajos.registry import ShortcutRegistry, merge_user_app_shortcuts
from shell.servicios.atajos.user_apps import (
    PROTECTED_KEY_SIGNATURES,
    UserAppShortcut,
    load_user_app_shortcuts,
    validate_chord,
)


class UserAppShortcutsTests(unittest.TestCase):
    def test_protected_system_chords(self) -> None:
        for signature in (
            "SUPER+J",
            "SUPER+K",
            "SUPER+F",
            "SUPER+1",
            "SUPER+SHIFT+1",
            "SUPER+Q",
            "SUPER+SPACE",
            "SUPER+ENTER",
            "SUPER+W",
        ):
            self.assertIn(signature, PROTECTED_KEY_SIGNATURES)

        self.assertIsNotNone(validate_chord(("SUPER",), "J"))
        self.assertIsNotNone(validate_chord(("SUPER", "SHIFT"), "F"))
        self.assertIsNotNone(validate_chord(("SUPER",), "1"))
        # SUPER+B is free for apps
        self.assertIsNone(validate_chord(("SUPER",), "B"))

    def test_round_trip_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "user-app-shortcuts.json"
            conf = Path(temporary) / "hyprland.conf"
            conf.write_text("general { layout = monocle }\n", encoding="utf-8")
            created = add_user_app_shortcut(
                app_id="firefox",
                app_name="Firefox",
                mods=("SUPER",),
                key="B",
                path=path,
                conf_path=conf,
                sync=True,
                reload=False,
            )
            loaded = load_user_app_shortcuts(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].app_id, "firefox")
            self.assertEqual(loaded[0].keys_display, "SUPER + B")
            text = conf.read_text(encoding="utf-8")
            self.assertIn(MARKER_BEGIN, text)
            self.assertIn(MARKER_END, text)
            self.assertIn("gtk-launch firefox", text)
            self.assertIn(created.hypr_bind_line(), text)

            update_user_app_shortcut(
                created.id,
                key="N",
                path=path,
                conf_path=conf,
                reload=False,
            )
            loaded = load_user_app_shortcuts(path)
            self.assertEqual(loaded[0].key, "N")
            self.assertIn("SUPER, N, exec, gtk-launch firefox", conf.read_text(encoding="utf-8"))

            self.assertTrue(
                remove_user_app_shortcut(
                    created.id, path=path, conf_path=conf, reload=False
                )
            )
            self.assertEqual(load_user_app_shortcuts(path), ())
            # Block remains but empty of binds
            text = conf.read_text(encoding="utf-8")
            self.assertIn(MARKER_BEGIN, text)
            self.assertNotIn("gtk-launch", text)

    def test_rejects_protected_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "user-app-shortcuts.json"
            conf = Path(temporary) / "hyprland.conf"
            conf.write_text("", encoding="utf-8")
            with self.assertRaises(UserAppShortcutError):
                add_user_app_shortcut(
                    app_id="x",
                    app_name="X",
                    mods=("SUPER",),
                    key="J",
                    path=path,
                    conf_path=conf,
                    reload=False,
                )
            add_user_app_shortcut(
                app_id="a",
                app_name="A",
                mods=("SUPER",),
                key="B",
                path=path,
                conf_path=conf,
                reload=False,
            )
            with self.assertRaises(UserAppShortcutError):
                add_user_app_shortcut(
                    app_id="b",
                    app_name="B",
                    mods=("SUPER",),
                    key="B",
                    path=path,
                    conf_path=conf,
                    reload=False,
                )

    def test_render_block_and_registry_overlay(self) -> None:
        items = (
            UserAppShortcut(
                id="abc",
                app_id="kitty",
                app_name="Kitty",
                mods=("SUPER",),
                key="T",
            ),
        )
        block = render_managed_block(items)
        self.assertIn("bind = SUPER, T, exec, gtk-launch kitty", block)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "user-app-shortcuts.json"
            from shell.servicios.atajos.user_apps import save_user_app_shortcuts

            save_user_app_shortcuts(items, path)
            payload = [
                {
                    "modmask": 64,
                    "key": "T",
                    "dispatcher": "exec",
                    "arg": "gtk-launch kitty",
                    "mouse": False,
                    "submap": "",
                },
                {
                    "modmask": 64,
                    "key": "F",
                    "dispatcher": "fullscreen",
                    "arg": "0",
                    "mouse": False,
                    "submap": "",
                },
            ]
            snapshot = ShortcutRegistry(hyprctl_runner=lambda _cmd: payload).load()
            # Force merge with explicit path via helper
            from shell.servicios.atajos.hyprland import parse_hyprctl_binds, raw_bind_to_entry

            raw_entries = []
            for index, bind in enumerate(parse_hyprctl_binds(payload)):
                entry = raw_bind_to_entry(bind, index=index)
                if entry is not None:
                    raw_entries.append(entry)
            merged = merge_user_app_shortcuts(tuple(raw_entries), user_path=path)
            user_rows = [item for item in merged if item.id.startswith("user-app:")]
            self.assertEqual(len(user_rows), 1)
            self.assertEqual(user_rows[0].description, "Kitty")
            self.assertTrue(user_rows[0].editable)
            self.assertTrue(any(item.action == "fullscreen" for item in merged))
            # Chord SUPER+T should not be duplicated
            self.assertEqual(
                sum(1 for item in merged if item.keys == "SUPER + T"), 1
            )


if __name__ == "__main__":
    unittest.main()
