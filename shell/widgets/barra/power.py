"""System power button, dropdown menu, and inline confirmations."""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import POWER_ICON_SIZE, POWER_MENU_OFFSET
from ...eventbus import EventBus
from ...runtime_relaunch import relaunch_shell
from ...servicios.energia.power import (
    ACTION_LOCK,
    ACTION_LOGOUT,
    ACTION_REBOOT,
    ACTION_SHUTDOWN,
    ACTION_SUSPEND,
    PowerService,
)
from ...popup_handle import (
    PopupHandle,
    PopupOutsideDismiss,
    pointer_inside_widget,
    present_popup,
    hide_popup,
)
from ...popup_spawn import publish_popup_spawn
from ...ui.starfield import install_starfield, resolve_event_bus
from ...ui import ShellModule
from ...window_identity import (
    TITLE_POWER_MENU,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)

MenuEntry = tuple[str, str, str, bool]

ACTION_RELAUNCH_SHELL = "relaunch_shell"
POWER_CONTROL_CENTER_REQUESTED = "power-control-center-requested"

POWER_MENU_ENTRIES: tuple[MenuEntry, ...] = (
    (ACTION_LOCK, "Bloquear", "system-lock-screen-symbolic", False),
    (ACTION_SUSPEND, "Suspender", "system-suspend-symbolic", False),
    (ACTION_RELAUNCH_SHELL, "Reiniciar Jugoo", "view-refresh-symbolic", False),
    (ACTION_LOGOUT, "Cerrar sesión", "system-log-out-symbolic", True),
    (ACTION_REBOOT, "Reiniciar", "system-reboot-symbolic", True),
    (ACTION_SHUTDOWN, "Apagar", "system-shutdown-symbolic", True),
)

_CONFIRM_COPY = {
    ACTION_LOGOUT: ("¿Cerrar sesión?", "Cerrar sesión"),
    ACTION_REBOOT: ("¿Reiniciar el equipo?", "Reiniciar"),
    ACTION_SHUTDOWN: ("¿Apagar el equipo?", "Apagar"),
}


class PowerMenu(Gtk.Window):
    """Compact action list anchored below the power button with inline confirmations."""

    _CONTENT_WIDTH = 270

    def __init__(
        self,
        shell_window: Gtk.Window,
        on_action_selected: Callable[[str], None],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._on_action_selected = on_action_selected
        self._anchor_button: Gtk.Widget | None = None
        self._needs_confirmation: dict[str, bool] = {}

        self.set_name("shell-power-menu")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_POWER_MENU)
        configure_interactive_popup(self)
        self.set_resizable(False)
        self.set_default_size(self._CONTENT_WIDTH, -1)

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._box.set_size_request(self._CONTENT_WIDTH, -1)
        self._box.get_style_context().add_class("power-menu-content")
        install_starfield(
            self,
            self._box,
            resolve_event_bus(shell_window),
        )
        self._render_menu_entries()

    def _render_menu_entries(self) -> None:
        self._clear_content()
        for action, label, icon_name, destructive in POWER_MENU_ENTRIES:
            self._box.pack_start(
                self._make_row(action, label, icon_name, destructive),
                False,
                False,
                0,
            )
        self._box.show_all()

    def _clear_content(self) -> None:
        for child in list(self._box.get_children()):
            child.destroy()

    def show_confirmation(self, action: str) -> None:
        self._clear_content()
        message, confirm_label = _CONFIRM_COPY[action]

        label = Gtk.Label(label=message, xalign=0)
        label.get_style_context().add_class("power-confirm-message")
        self._box.pack_start(label, False, False, 10)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        actions.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label="Cancelar")
        cancel_btn.get_style_context().add_class("power-confirm-cancel")
        cancel_btn.connect("clicked", lambda *_args: self._on_action_selected("cancel"))
        actions.pack_start(cancel_btn, False, False, 0)

        confirm_btn = Gtk.Button(label=confirm_label)
        confirm_btn.get_style_context().add_class("power-confirm-action")
        confirm_btn.get_style_context().add_class(
            "power-confirm-action-destructive" if action in (ACTION_LOGOUT, ACTION_REBOOT, ACTION_SHUTDOWN) else ""
        )
        confirm_btn.connect("clicked", lambda *_args: self._on_action_selected(f"confirm:{action}"))
        actions.pack_start(confirm_btn, False, False, 0)

        self._box.pack_start(actions, False, False, 0)
        self._box.show_all()
        self.set_default_size(self._CONTENT_WIDTH, -1)

    def set_default_action_state(self) -> None:
        self._render_menu_entries()

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        publish_popup_spawn(
            self,
            anchor_button,
            title=TITLE_POWER_MENU,
            offset=POWER_MENU_OFFSET,
        )
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_menu(self) -> None:
        self._anchor_button = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def set_needs_confirmation(self, action: str, needs: bool) -> None:
        self._needs_confirmation[action] = needs

    def get_needs_confirmation(self, action: str) -> bool:
        return self._needs_confirmation.get(action, False)

    def _make_row(
        self,
        action: str,
        label: str,
        icon_name: str,
        destructive: bool,
    ) -> Gtk.Button:
        button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        style = button.get_style_context()
        style.add_class("power-menu-item")
        if destructive:
            style.add_class("power-menu-item-destructive")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        icon.set_pixel_size(POWER_ICON_SIZE)
        row.pack_start(icon, False, False, 0)
        row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
        button.add(row)
        button.connect(
            "clicked",
            lambda _btn, act=action: self._on_action_selected(act),
        )
        return button

    def _position_after_show(self) -> bool:
        if self._anchor_button is not None:
            position_popup_below_anchor(
                self,
                self._anchor_button,
                title=TITLE_POWER_MENU,
                offset=POWER_MENU_OFFSET,
            )
        return False


class PowerWidget(ShellModule):
    """Power button embedded in the main bar; confirmations are inline in the menu."""

    def __init__(
        self,
        power_service: PowerService,
        shell_window: Gtk.Window,
        event_bus: EventBus,
    ) -> None:
        super().__init__("power-widget", spacing=0)

        self._power_service = power_service
        self._shell_window = shell_window
        self._event_bus = event_bus
        self._pending_action: str | None = None

        self._button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self._button.get_style_context().add_class("power-button")
        icon = Gtk.Image.new_from_icon_name(
            "system-shutdown-symbolic",
            Gtk.IconSize.MENU,
        )
        self._icon = icon
        icon.set_pixel_size(POWER_ICON_SIZE)
        self._button.add(icon)
        self._button.connect("button-press-event", self._on_button_press)
        self._button.connect("clicked", self._on_button_clicked)
        self.pack_start(self._button, False, False, 0)

        self._menu = PopupHandle(self._create_menu)
        self._outside_click = PopupOutsideDismiss()

    def get_anchor_button(self) -> Gtk.Widget:
        return self._button

    def _create_menu(self) -> PowerMenu:
        return PowerMenu(self._shell_window, self._on_menu_action_selected)

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            self._event_bus.emit(POWER_CONTROL_CENTER_REQUESTED, self._button)
            return True
        return False

    def _on_button_clicked(self, *_args) -> None:
        self._toggle_power_menu()

    def _toggle_power_menu(self) -> None:
        if self._menu.is_visible():
            self.close_menu()
            return
        menu = self._menu.get()
        menu.open_for(self._button)
        self._outside_click.install(
            menu,
            self._shell_window,
            (self._button,),
            self.close_menu,
            self._event_bus,
        )
        # Mark destructive actions as needing confirmation
        for action, label, icon_name, destructive in POWER_MENU_ENTRIES:
            if destructive:
                menu.set_needs_confirmation(action, True)
            else:
                menu.set_needs_confirmation(action, False)

    def get_needs_confirmation(self, action: str) -> bool:
        menu = self._menu.maybe
        if menu is None:
            return False
        return menu.get_needs_confirmation(action)

    def _on_menu_action_selected(self, action: str) -> None:
        if action == "cancel":
            menu = self._menu.maybe
            if menu is not None:
                menu.set_default_action_state()
            return
        if action.startswith("confirm:"):
            self._execute_action(action.removeprefix("confirm:"))
            self.close_menu()
            return
        if self.get_needs_confirmation(action):
            # Show inline confirmation by replacing menu content
            self._show_inline_confirmation(action)
        else:
            self._execute_action(action)
            self.close_menu()

    def _show_inline_confirmation(self, action: str) -> None:
        """Replace the menu content with inline confirmation buttons in the same popup."""
        self._menu.get().show_confirmation(action)

    def _on_inline_confirm(self, action: str) -> None:
        """Handle inline confirmation button click."""
        self._execute_action(action)
        self.close_menu()

    def _on_inline_cancel(self, _action: str) -> None:
        """Handle inline cancellation - just restore the normal menu items."""
        menu = self._menu.maybe
        if menu is not None:
            menu.set_default_action_state()

    def _execute_action(self, action: str) -> None:
        if action == ACTION_RELAUNCH_SHELL:
            self.close_menu()
            GLib.idle_add(self._relaunch_shell)
            return
        handler = {
            ACTION_LOCK: self._power_service.lock,
            ACTION_SUSPEND: self._power_service.suspend,
            ACTION_LOGOUT: self._power_service.logout,
            ACTION_REBOOT: self._power_service.reboot,
            ACTION_SHUTDOWN: self._power_service.shutdown,
        }.get(action)
        if handler is None:
            return
        # Suspend waits for hyprlock; keep the GTK loop responsive.
        if action == ACTION_SUSPEND:
            threading.Thread(
                target=self._run_power_handler,
                args=(action, handler),
                daemon=True,
                name="jugoo-suspend",
            ).start()
            return
        self._run_power_handler(action, handler)

    def _run_power_handler(self, action: str, handler: Callable[[], None]) -> None:
        try:
            handler()
        except Exception as error:
            print(f"shell: power action {action} failed: {error}")

    def _relaunch_shell(self) -> bool:
        try:
            relaunch_shell()
        except OSError as error:
            print(f"shell: relaunch failed: {error}")
        return False

    def close_menu(self) -> None:
        self._outside_click.uninstall()
        menu = self._menu.maybe
        if menu is not None:
            menu.close_menu()
        self._pending_action = None


def _pointer_inside_widget(widget: Gtk.Widget) -> bool:
    return pointer_inside_widget(widget)