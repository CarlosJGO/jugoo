"""Settings store persistence and schema sanity checks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shell.settings.layout_model import DEFAULT_LAYOUT, parse_layout
from shell.settings.schema import CategoryId, build_settings_catalog, settings_by_key
from shell.settings.store import SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip_persists_overrides_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            store = SettingsStore(path)
            store.load()
            self.assertTrue(store.set("modo_noche.enabled", True))
            self.assertTrue(store.set("modo_noche.temperature", 3200))
            store.save()

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["values"]["modo_noche.temperature"], 3200)
            self.assertNotIn("widgets.clock_time_format", payload["values"])

            reloaded = SettingsStore(path)
            reloaded.load()
            self.assertTrue(reloaded.get("modo_noche.enabled"))
            self.assertEqual(reloaded.get("modo_noche.temperature"), 3200)

    def test_rejects_out_of_range_with_clamp(self) -> None:
        store = SettingsStore(Path("/tmp/jugoo-settings-unused.json"))
        store.set("modo_noche.temperature", 9000)
        self.assertEqual(store.get("modo_noche.temperature"), 6500)

    def test_catalog_has_unique_keys(self) -> None:
        catalog = build_settings_catalog()
        keys = [item.key for item in catalog]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(CategoryId.MODO_NOCHE, {item.category for item in catalog})
        self.assertIn("tema.active", settings_by_key())
        self.assertIn("notificaciones.grouping_mode", settings_by_key())
        self.assertIn("notificaciones.grouping_exceptions", settings_by_key())

    def test_layout_defaults_include_settings_slot(self) -> None:
        ids = {slot.id for slot in DEFAULT_LAYOUT}
        self.assertIn("settings", ids)
        self.assertEqual(parse_layout(""), DEFAULT_LAYOUT)


if __name__ == "__main__":
    unittest.main()
