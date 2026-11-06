from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest import mock

from shell.widgets.aplicaciones.radial_layout import radial_layout
from shell.models import ApplicationsSnapshot, DesktopApplication
from shell.eventbus import EventBus

_OUTPUTS_AND_COUNTS = (
    ((1920, 1080), (1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64)),
    ((1366, 768), (1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48)),
    ((1280, 1024), (1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64)),
    ((800, 600), (1, 2, 3, 4, 6, 8, 12, 16, 20, 24)),
    ((320, 240), (1, 2, 3, 4, 6, 8)),
)


class RadialLayoutTests(unittest.TestCase):
    def test_pinned_snapshot_preserves_order_and_omits_missing_apps(self) -> None:
        firefox = DesktopApplication(
            id="firefox",
            name="Firefox",
            icon="firefox",
        )
        editor = DesktopApplication(
            id="editor",
            name="Editor",
            icon="accessories-text-editor",
        )
        snapshot = ApplicationsSnapshot(
            applications=(firefox, editor),
            pinned_ids=("editor", "missing", "firefox"),
        )

        self.assertEqual(snapshot.pinned_apps(), (editor, firefox))

    def test_radial_layout_fits_full_circle_app_sectors(self) -> None:
        for (width, height), counts in _OUTPUTS_AND_COUNTS:
            for count in counts:
                with self.subTest(output=(width, height), count=count):
                    layout = radial_layout(count, width, height)

                    self.assertEqual(len(layout.positions), count)
                    self.assertGreater(layout.node_size, 0)
                    self.assertGreaterEqual(layout.center_x - layout.half_width, 0)
                    self.assertLessEqual(layout.center_x + layout.half_width, width)
                    self.assertGreaterEqual(layout.center_y - layout.half_height, 0)
                    self.assertLessEqual(layout.center_y + layout.half_height, height)
                    sector_angles: list[float] = []
                    for position in layout.positions:
                        sector_angles.append(
                            position.end_angle - position.start_angle
                        )
                        self.assertEqual(position.inner_radius, 0)
                        self.assertGreater(position.outer_radius, 0)
                        self.assertAlmostEqual(
                            math.hypot(
                                position.center_x - layout.center_x,
                                position.center_y - layout.center_y,
                            ),
                            position.outer_radius * 0.68,
                        )
                        half = position.size / 2
                        self.assertLessEqual(half, position.center_x)
                        self.assertLessEqual(position.center_x, width - half)
                        self.assertLessEqual(half, position.center_y)
                        self.assertLessEqual(position.center_y, height - half)
                    for index, left in enumerate(layout.positions):
                        for right in layout.positions[index + 1 :]:
                            distance = math.hypot(
                                right.center_x - left.center_x,
                                right.center_y - left.center_y,
                            )
                            half_sum = (left.size + right.size) / 2
                            self.assertGreaterEqual(distance + 1e-6, half_sum)
                    self.assertAlmostEqual(sum(sector_angles), math.tau)

    def test_clamping_preserves_opening_origin(self) -> None:
        for origin in (
            (0, 0),
            (1920, 0),
            (0, 1080),
            (1920, 1080),
            (8, 540),
            (1912, 540),
        ):
            with self.subTest(origin=origin):
                layout = radial_layout(12, 1920, 1080, origin=origin)

                self.assertEqual((layout.origin_x, layout.origin_y), origin)
                self.assertNotEqual(
                    (layout.center_x, layout.center_y),
                    origin,
                )
                self.assertGreaterEqual(layout.center_x - layout.half_width, 0)
                self.assertLessEqual(
                    layout.center_x + layout.half_width,
                    1920,
                )
                self.assertGreaterEqual(layout.center_y - layout.half_height, 0)
                self.assertLessEqual(
                    layout.center_y + layout.half_height,
                    1080,
                )
                for position in layout.positions:
                    half = position.size / 2
                    self.assertLessEqual(half, position.center_x)
                    self.assertLessEqual(position.center_x, 1920 - half)
                    self.assertLessEqual(half, position.center_y)
                    self.assertLessEqual(position.center_y, 1080 - half)

    def test_center_origin_needs_no_shift_for_a_normal_layout(self) -> None:
        layout = radial_layout(8, 1920, 1080)

        self.assertEqual((layout.origin_x, layout.origin_y), (960, 540))
        self.assertEqual((layout.center_x, layout.center_y), (960, 540))

    def test_typical_radial_menu_stays_compact(self) -> None:
        layout = radial_layout(8, 1920, 1080)

        self.assertLessEqual(layout.node_size, 54)
        self.assertLessEqual(layout.half_width * 2, 320)
        self.assertLessEqual(layout.half_height * 2, 320)

    def test_single_application_uses_a_tighter_radius(self) -> None:
        single = radial_layout(1, 1920, 1080)
        several = radial_layout(8, 1920, 1080)

        self.assertLess(single.half_height * 2, several.half_height * 2)

    def test_large_pinned_sets_keep_sector_menu_bounded(self) -> None:
        typical = radial_layout(8, 1920, 1080)
        crowded = radial_layout(64, 1920, 1080)

        self.assertLess(crowded.node_size, typical.node_size)
        self.assertLess(crowded.half_width * 2, 650)
        self.assertLess(crowded.half_height * 2, 650)

    def test_empty_state_geometry_is_clamped(self) -> None:
        layout = radial_layout(0, 320, 240, origin=(0, 0))

        self.assertEqual(layout.positions, ())
        self.assertGreaterEqual(layout.center_x, layout.half_width)
        self.assertGreaterEqual(layout.center_y, 21)
        self.assertLessEqual(layout.center_x + layout.half_width, 320)
        self.assertLessEqual(layout.center_y + layout.half_height, 240)
        self.assertEqual(layout.center_x, 100)
        self.assertEqual(layout.center_y, 36)

    def test_pinned_bar_uses_one_button_and_keeps_apps_manageable(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")
        from shell.widgets.barra.pinned_apps import PinnedAppsWidget

        firefox = DesktopApplication(id="firefox", name="Firefox", icon="firefox")
        editor = DesktopApplication(id="editor", name="Editor", icon="accessories-text-editor")
        calls: list[str] = []
        shell_window = Gtk.Window()
        widget = PinnedAppsWidget(
            EventBus(),
            shell_window,
            on_radial_launcher=lambda: calls.append("open"),
        )
        try:
            widget._apply_snapshot(
                ApplicationsSnapshot(
                    applications=(firefox, editor),
                    pinned_ids=("firefox", "editor"),
                )
            )
            self.assertEqual(widget.get_children(), [widget._launcher_button])
            self.assertEqual(set(widget._buttons), {"firefox", "editor"})
            widget._launcher_button.emit("clicked")
            self.assertEqual(calls, ["open"])
            updated_firefox = DesktopApplication(
                id="firefox",
                name="Firefox Nightly",
                icon="firefox-nightly",
            )
            widget._apply_snapshot(
                ApplicationsSnapshot(
                    applications=(updated_firefox, editor),
                    pinned_ids=("firefox", "editor"),
                )
            )
            self.assertEqual(widget._buttons["firefox"].get_tooltip_text(), "Firefox Nightly")
            self.assertEqual(set(widget._buttons), {"firefox", "editor"})
        finally:
            widget.destroy()
            shell_window.destroy()

    def test_invalid_geometry_is_rejected(self) -> None:
        for args in ((-1, 800, 600), (1, 0, 600), (1, 800, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                radial_layout(*args)
        with self.assertRaisesRegex(ValueError, "finite"):
            radial_layout(1, 800, 600, origin=(math.nan, 0))

    def test_overlay_can_be_constructed_on_a_gtk_display(self) -> None:
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gdk, Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")
        import cairo

        from shell.models import DesktopApplication
        from shell.widgets.aplicaciones.radial_launcher import RadialLauncherOverlay

        shell_window = Gtk.Window()
        selected: list[DesktopApplication] = []
        overlay = RadialLauncherOverlay(shell_window, on_activate=selected.append)
        try:
            applications = (
                DesktopApplication(id="firefox.desktop", name="Firefox", icon="firefox"),
                DesktopApplication(
                    id="editor.desktop",
                    name="Editor",
                    icon="accessories-text-editor",
                ),
            )
            overlay.set_applications(applications)
            self.assertEqual(overlay.applications, applications)
            self.assertEqual(overlay.get_title(), "Jugoo Radial Launcher")
            self.assertEqual(overlay.layout, None)
            overlay._width, overlay._height = 800, 600
            overlay._set_origin((0, 0))
            overlay._progress = 1.0
            allocation = Gdk.Rectangle()
            allocation.x = allocation.y = 0
            allocation.width, allocation.height = 800, 600
            overlay._canvas.size_allocate(allocation)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 600)
            overlay._draw(overlay._canvas, cairo.Context(surface))
            surface.flush()

            layout = overlay.layout
            self.assertIsNotNone(layout)
            assert layout is not None
            target = layout.positions[0]
            angle = (target.start_angle + target.end_angle) / 2
            sample_radius = target.outer_radius * 0.92
            sample_x = layout.center_x + math.cos(angle) * sample_radius
            sample_y = layout.center_y + math.sin(angle) * sample_radius
            self.assertGreater(
                math.hypot(
                    sample_x - target.center_x,
                    sample_y - target.center_y,
                ),
                target.size / 2,
            )
            self.assertEqual(
                overlay._hit_test(sample_x, sample_y),
                0,
            )
            adjacent = layout.positions[1]
            adjacent_angle = (adjacent.start_angle + adjacent.end_angle) / 2
            self.assertEqual(
                overlay._hit_test(
                    layout.center_x + math.cos(adjacent_angle) * sample_radius,
                    layout.center_y + math.sin(adjacent_angle) * sample_radius,
                ),
                1,
            )
            with mock.patch.object(overlay, "close_animated") as close:
                self.assertTrue(
                    overlay._on_click(
                        overlay._canvas,
                        SimpleNamespace(
                            button=1,
                            x=layout.center_x,
                            y=layout.center_y,
                        ),
                    )
                )
                close.assert_called_once_with()

                close.reset_mock()
                self.assertTrue(
                    overlay._on_key_press(
                        overlay,
                        SimpleNamespace(keyval=Gdk.KEY_Escape),
                    )
                )
                close.assert_called_once_with()

            overlay._activation_dispatched = False
            overlay._anim_to = 1.0
            with (
                mock.patch.object(overlay, "get_visible", return_value=True),
                mock.patch.object(overlay, "close_animated") as close,
            ):
                self.assertTrue(
                    overlay._on_click(
                        overlay._canvas,
                        SimpleNamespace(
                            button=1,
                            x=sample_x,
                            y=sample_y,
                        ),
                    )
                )
                self.assertEqual(selected, [applications[0]])
                close.assert_called_once_with()
            overlay._activate_selected()
            overlay._activate_selected()
            self.assertEqual(selected, [applications[0]])
        finally:
            overlay.destroy()
            shell_window.destroy()

    def test_animation_duration_uses_theme_and_global_motion_gate(self) -> None:
        from types import SimpleNamespace

        from shell.widgets.aplicaciones import radial_launcher

        previous = radial_launcher.active_theme
        try:
            radial_launcher.active_theme = lambda: None
            self.assertEqual(radial_launcher.animation_duration_ms(), 190)
            radial_launcher.active_theme = lambda: SimpleNamespace(
                animation=SimpleNamespace(enabled=False, duration=180)
            )
            self.assertEqual(radial_launcher.animation_duration_ms(), 0)
            radial_launcher.active_theme = lambda: SimpleNamespace(
                animation=SimpleNamespace(enabled=True, duration=80)
            )
            self.assertEqual(radial_launcher.animation_duration_ms(), 140)
            radial_launcher.active_theme = lambda: SimpleNamespace(
                animation=SimpleNamespace(enabled=True, duration=300)
            )
            self.assertEqual(radial_launcher.animation_duration_ms(), 240)
        finally:
            radial_launcher.active_theme = previous


if __name__ == "__main__":
    unittest.main()
