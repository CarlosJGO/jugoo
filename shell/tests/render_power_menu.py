"""Render the shell bar with the power menu open for visual inspection."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk

from shell.app import ShellApplication
from shell.servicios.energia.power import PowerService


def main() -> None:
    captured: dict[str, str] = {"path": ""}

    def open_menu(_app: ShellApplication) -> bool:
        _app.power_widget._ensure_shell_press_handler()
        _app.power_widget._menu.open_for(_app.power_widget._button)
        return False

    def capture(app: ShellApplication) -> bool:
        window = app.get_window()
        if window is None:
            Gtk.main_quit()
            return False

        while Gtk.events_pending():
            Gtk.main_iteration()

        origin = window.get_origin()
        if len(origin) == 3:
            _, x, y = origin
        else:
            x, y = origin

        allocation = app.get_allocation()
        width = max(allocation.width, 1)
        height = max(allocation.height, 1) + 220
        captured["path"] = "/tmp/shell_power_menu.png"

        import subprocess

        geometry = f"{x},{y} {width}x{height}"
        result = subprocess.run(
            ["grim", "-g", geometry, captured["path"]],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"captured {captured['path']} ({geometry})")
        else:
            print(result.stderr.strip() or "grim capture failed")
        Gtk.main_quit()
        return False

    app = ShellApplication()
    app.power_service = PowerService(dry_run=True)
    app.power_widget._power_service = app.power_service
    GLib.timeout_add(1200, open_menu, app)
    GLib.timeout_add(1800, capture, app)
    Gtk.main()


if __name__ == "__main__":
    main()
