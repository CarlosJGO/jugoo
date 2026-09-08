"""Shaped disguise silhouette and mask helpers."""

from __future__ import annotations

import unittest

import cairo

from shell.ui.disfraces.asteroid import AsteroidDisguise
from shell.ui.disfraces.mask import mask_surface_from_shape
from shell.ui.disfraces.meteor import MeteorDisguise
from shell.ui.disfraces import DisguiseId, WindowRole, disguise_for


class ShapedDisguiseTests(unittest.TestCase):
    def test_roles(self) -> None:
        self.assertEqual(disguise_for(WindowRole.NOTIFICATIONS_POPUP), DisguiseId.NORMAL)
        self.assertEqual(disguise_for(WindowRole.NOTIFICATION_TOAST), DisguiseId.NORMAL)
        # Implementations still exist for a future revisit.
        self.assertIs(AsteroidDisguise().id, DisguiseId.ASTEROID)
        self.assertIs(MeteorDisguise().id, DisguiseId.METEOR)

    def test_asteroid_mask_is_not_a_full_rectangle(self) -> None:
        disguise = AsteroidDisguise()
        width, height = 440, 360
        mask = mask_surface_from_shape(width, height, disguise.build_shape)
        data = bytes(mask.get_data())
        # A8 stride may be aligned; count opaque-ish pixels.
        opaque = sum(1 for value in data if value > 32)
        total = width * height
        # Silhouette must leave a meaningful transparent margin.
        self.assertLess(opaque, int(total * 0.92))
        self.assertGreater(opaque, int(total * 0.35))

        # Corners of the bounding box should be outside the blob.
        stride = mask.get_stride()

        def sample(x: int, y: int) -> int:
            return data[y * stride + x]

        self.assertLess(sample(1, 1), 16)
        self.assertLess(sample(width - 2, 1), 16)
        self.assertLess(sample(1, height - 2), 16)
        self.assertLess(sample(width - 2, height - 2), 16)
        # Center should be solid rock.
        self.assertGreater(sample(width // 2, height // 2), 200)

    def test_meteor_mask_has_left_trail_bias(self) -> None:
        disguise = MeteorDisguise()
        width, height = 400, 120
        mask = mask_surface_from_shape(width, height, disguise.build_shape)
        data = bytes(mask.get_data())
        stride = mask.get_stride()
        left = sum(
            1
            for y in range(height)
            for x in range(0, width // 5)
            if data[y * stride + x] > 32
        )
        right = sum(
            1
            for y in range(height)
            for x in range(3 * width // 5, width)
            if data[y * stride + x] > 32
        )
        self.assertGreater(right, left)


if __name__ == "__main__":
    unittest.main()
