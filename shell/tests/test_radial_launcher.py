from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest import mock

from shell.widgets.aplicaciones.radial_layout import radial_layout
from shell.models import ApplicationsSnapshot, DesktopApplication
from shell.eventbus import EventBus

_OUTPUTS_AND_COUNTS = (
    ((1920, 1080), (1, 2, 3, 4, 6, 8, 12, 13, 16, 20, 24, 25, 32, 48, 64)),
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
                    rings: dict[int, list[float]] = {}
                    for position in layout.positions:
                        rings.setdefault(position.ring, []).append(
                            position.end_angle - position.start_angle
                        )
                        self.assertGreaterEqual(position.inner_radius, 0)
                        self.assertGreater(position.outer_radius, 0)
                        self.assertLess(
                            position.inner_radius,
                            position.outer_radius,
                        )
                        if position.ring == 0:
                            self.assertEqual(position.inner_radius, 0)
                            expected_icon_radius = position.outer_radius * 0.68
                        else:
                            expected_icon_radius = (
                                position.inner_radius + position.outer_radius
                            ) / 2
                        self.assertAlmostEqual(
                            math.hypot(
                                position.center_x - layout.center_x,
                                position.center_y - layout.center_y,
                            ),
                            expected_icon_radius,
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
                    self.assertEqual(len(rings), layout.ring_count)
                    self.assertTrue(
                        all(len(angles) <= 12 for angles in rings.values())
                    )
                    for angles in rings.values():
                        self.assertAlmostEqual(sum(angles), math.tau)

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
        self.assertEqual(crowded.ring_count, 6)
        self.assertLess(crowded.half_width * 2, 650)
        self.assertLess(crowded.half_height * 2, 650)
        with self.assertRaisesRegex(ValueError, "do not fit"):
            radial_layout(64, 320, 240)

    def test_outer_rings_are_added_after_twelve_apps_and_balanced(self) -> None:
        one_ring = radial_layout(12, 1920, 1080)
        two_rings = radial_layout(13, 1920, 1080)
        three_rings = radial_layout(25, 1920, 1080)

        self.assertEqual(one_ring.ring_count, 1)
        self.assertEqual(two_rings.ring_count, 2)
        self.assertEqual(
            [sum(position.ring == ring for position in two_rings.positions) for ring in range(2)],
            [7, 6],
        )
        self.assertEqual(three_rings.ring_count, 3)
        self.assertEqual(
            [
                sum(position.ring == ring for position in three_rings.positions)
                for ring in range(3)
            ],
            [9, 8, 8],
        )
        for layout in (two_rings, three_rings):
            rings = {
                ring: [position for position in layout.positions if position.ring == ring]
                for ring in range(layout.ring_count)
            }
            for ring in range(1, layout.ring_count):
                inner = rings[ring][0].inner_radius
                self.assertEqual(inner, rings[ring - 1][0].outer_radius)

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
        new_instances: list[DesktopApplication] = []
        overlay = RadialLauncherOverlay(
            shell_window,
            on_activate=selected.append,
            on_new_instance=new_instances.append,
        )
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
                self.assertEqual(selected, [])

                close.reset_mock()
                self.assertTrue(
                    overlay._on_click(
                        overlay._canvas,
                        SimpleNamespace(
                            button=1,
                            x=799,
                            y=599,
                        ),
                    )
                )
                close.assert_called_once_with()
                self.assertEqual(selected, [])

                close.reset_mock()
                self.assertTrue(
                    overlay._on_key_press(
                        overlay,
                        SimpleNamespace(keyval=Gdk.KEY_Escape),
                    )
                )
                close.assert_called_once_with()

            overlay._selection_dispatched = False
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
                close.reset_mock()
                overlay._selection_dispatched = False
                self.assertTrue(
                    overlay._on_click(
                        overlay._canvas,
                        SimpleNamespace(
                            button=3,
                            x=sample_x,
                            y=sample_y,
                        ),
                    )
                )
                self.assertEqual(selected, [applications[0]])
                self.assertEqual(new_instances, [applications[0]])
                close.assert_called_once_with()
            overlay._activate_selected()
            overlay._activate_selected()
            self.assertEqual(selected, [applications[0]])
            overlay._new_instance_selected()
            overlay._new_instance_selected()
            self.assertEqual(new_instances, [applications[0]])
        finally:
            overlay.destroy()
            shell_window.destroy()

    def test_outer_ring_sector_selects_app_outside_icon_hitbox(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")

        from shell.widgets.aplicaciones.radial_launcher import RadialLauncherOverlay

        shell_window = Gtk.Window()
        overlay = RadialLauncherOverlay(shell_window)
        try:
            applications = tuple(
                DesktopApplication(
                    id=f"app-{index}",
                    name=f"App {index}",
                    icon="application-x-executable",
                )
                for index in range(14)
            )
            overlay.set_applications(applications)
            overlay._width, overlay._height = 800, 600
            overlay._set_origin((400, 300))
            overlay._progress = 1.0
            layout = overlay.layout
            self.assertIsNotNone(layout)
            assert layout is not None
            self.assertEqual(layout.ring_count, 2)

            target_index = next(
                index
                for index, position in enumerate(layout.positions)
                if position.ring == 1
            )
            target = layout.positions[target_index]
            angle = (target.start_angle + target.end_angle) / 2
            radius = target.inner_radius + (
                target.outer_radius - target.inner_radius
            ) * 0.85
            x = layout.center_x + math.cos(angle) * radius
            y = layout.center_y + math.sin(angle) * radius
            self.assertGreater(
                math.hypot(x - target.center_x, y - target.center_y),
                target.size / 2,
            )
            self.assertEqual(overlay._hit_test(x, y), target_index)
        finally:
            overlay.destroy()
            shell_window.destroy()

    def test_pinned_updates_are_deferred_until_overlay_closes(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")

        from shell.widgets.aplicaciones.radial_launcher import RadialLauncherOverlay

        shell_window = Gtk.Window()
        overlay = RadialLauncherOverlay(shell_window)
        try:
            initial = tuple(
                DesktopApplication(
                    id=f"app-{index}",
                    name=f"App {index}",
                    icon="application-x-executable",
                )
                for index in range(13)
            )
            overlay.set_applications(initial)
            overlay._width, overlay._height = 800, 600
            overlay._set_origin((400, 300))
            overlay._selected = 12
            updated = (
                DesktopApplication(
                    id="app-0",
                    name="Renamed app",
                    icon="updated-app-icon",
                ),
                *initial[1:],
                DesktopApplication(
                    id="app-13",
                    name="New app",
                    icon="application-x-executable",
                ),
            )
            overlay._progress = 1.0
            with mock.patch.object(overlay, "get_visible", return_value=True):
                overlay.set_applications(updated)

            self.assertEqual(overlay.applications, initial)
            self.assertEqual(overlay._pending_applications, updated)
            overlay._apply_pending_applications()
            self.assertEqual(overlay.applications, updated)
            self.assertEqual(overlay._selected, 12)
            self.assertEqual(overlay.layout.ring_count, 2)
            self.assertEqual(overlay.applications[0].icon, "updated-app-icon")
        finally:
            overlay.destroy()
            shell_window.destroy()

    def test_monitor_open_uses_its_logical_center_by_default(self) -> None:
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gdk, Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")
        display = Gdk.Display.get_default()
        if display is None or display.get_n_monitors() == 0:
            self.skipTest("GDK monitor unavailable")
        from shell.widgets.aplicaciones.radial_launcher import RadialLauncherOverlay

        shell_window = Gtk.Window()
        overlay = RadialLauncherOverlay(shell_window)
        monitor = display.get_monitor(0)
        geometry = monitor.get_geometry()
        try:
            overlay.set_applications(
                (DesktopApplication(id="firefox", name="Firefox", icon="firefox"),)
            )
            with (
                mock.patch.object(overlay, "show_all"),
                mock.patch.object(overlay, "present"),
                mock.patch.object(overlay._canvas, "grab_focus"),
                mock.patch.object(overlay, "_animate_to") as animate,
            ):
                overlay.open_on_monitor(monitor)

            self.assertEqual(
                (overlay._width, overlay._height),
                (geometry.width, geometry.height),
            )
            self.assertIsNotNone(overlay.layout)
            assert overlay.layout is not None
            self.assertEqual(
                (overlay.layout.origin_x, overlay.layout.origin_y),
                (geometry.width / 2, geometry.height / 2),
            )
            animate.assert_called_once_with(1.0)
        finally:
            overlay.destroy()
            shell_window.destroy()

    def test_shell_routes_focused_and_cursor_monitors_to_shared_controller(self) -> None:
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")
        display = Gdk.Display.get_default()
        if display is None or display.get_n_monitors() == 0:
            self.skipTest("GDK monitor unavailable")
        from shell import app as shell_app
        from shell.window_identity import MonitorRect

        monitor = display.get_monitor(0)
        geometry = monitor.get_geometry()
        calls: list[tuple[object, tuple[float, float] | None]] = []

        class ApplicationsController:
            radial_launcher_visible = False

            def toggle_radial_launcher(self, target, *, origin=None) -> None:
                calls.append((target, origin))

        class OverlayController:
            def close(self) -> None:
                pass

            def close_pickers(self) -> None:
                pass

            def close_panel(self) -> None:
                pass

            def close_popup(self) -> None:
                pass

        shell = SimpleNamespace(
            applications_controller=ApplicationsController(),
            ai_prompt_controller=OverlayController(),
            pickers_controller=OverlayController(),
            steam_controller=OverlayController(),
            control_center_controller=OverlayController(),
            settings_controller=OverlayController(),
        )
        cursor = (geometry.x + 37, geometry.y + 79)
        with mock.patch.object(
            shell_app,
            "hyprland_cursor_position",
            return_value=cursor,
        ):
            shell_app.ShellApplication._toggle_radial_launcher(
                shell,
                at_cursor=True,
            )
        self.assertEqual(calls, [(monitor, (37, 79))])

        calls.clear()
        focused_monitor = MonitorRect(
            x=geometry.x,
            y=geometry.y,
            width=geometry.width,
            height=geometry.height,
            focused=True,
        )
        with mock.patch.object(
            shell_app,
            "query_hyprland_monitors",
            return_value=(focused_monitor,),
        ):
            shell_app.ShellApplication._toggle_radial_launcher(
                shell,
                at_cursor=False,
            )
        self.assertEqual(calls, [(monitor, None)])

    def test_repeated_animation_request_reverses_from_current_progress(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk

        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")

        from shell.widgets.aplicaciones.radial_launcher import RadialLauncherOverlay

        shell_window = Gtk.Window()
        overlay = RadialLauncherOverlay(shell_window)
        try:
            with (
                mock.patch(
                    "shell.widgets.aplicaciones.radial_launcher.animation_duration_ms",
                    return_value=190,
                ),
                mock.patch.object(
                    overlay._canvas,
                    "add_tick_callback",
                    side_effect=(17, 18),
                ) as add_tick,
                mock.patch.object(
                    overlay._canvas,
                    "remove_tick_callback",
                ) as remove_tick,
            ):
                overlay._animate_to(1.0)
                self.assertTrue(overlay.animating)
                overlay._progress = 0.4
                overlay._animate_to(0.0)

                remove_tick.assert_called_once_with(17)
                self.assertEqual(add_tick.call_count, 2)
                self.assertEqual(overlay._anim_from, 0.4)
                self.assertEqual(overlay._anim_to, 0.0)
                self.assertTrue(overlay.animating)
        finally:
            overlay.destroy()

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
