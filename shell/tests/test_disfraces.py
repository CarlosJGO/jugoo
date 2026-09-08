"""Disguise catalog and apply helpers."""

from __future__ import annotations

import unittest

from shell.ui.disfraces import (
    DisguiseId,
    WindowRole,
    disguise_for,
    set_disguise,
)
from shell.ui.disfraces.catalog import all_assignments
from shell.ui.disfraces.apply import resolve


class DisguiseCatalogTests(unittest.TestCase):
    def test_notifications_use_spatial_costumes(self) -> None:
        # Costumes are dormant; all surfaces stay NORMAL until we revisit the look.
        self.assertEqual(disguise_for(WindowRole.NOTIFICATIONS_POPUP), DisguiseId.NORMAL)
        self.assertEqual(disguise_for(WindowRole.NOTIFICATION_TOAST), DisguiseId.NORMAL)
        self.assertEqual(disguise_for(WindowRole.SETTINGS), DisguiseId.NORMAL)

    def test_runtime_override(self) -> None:
        previous = disguise_for(WindowRole.MEDIA_POPUP)
        try:
            set_disguise(WindowRole.MEDIA_POPUP, DisguiseId.ASTEROID)
            self.assertEqual(disguise_for(WindowRole.MEDIA_POPUP), DisguiseId.ASTEROID)
        finally:
            set_disguise(WindowRole.MEDIA_POPUP, previous)

    def test_every_role_resolves(self) -> None:
        for role in WindowRole:
            disguise = disguise_for(role)
            self.assertIsNotNone(resolve(disguise))
        self.assertIn(WindowRole.NOTIFICATIONS_POPUP, all_assignments())


if __name__ == "__main__":
    unittest.main()
