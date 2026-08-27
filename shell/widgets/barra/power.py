"""System power button, dropdown menu, and confirmation dialogs."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, Gtk

from ...config import POWER_COMPACT_ICON_SIZE, POWER_ICON_SIZE, POWER_MENU_OFFSET
from ...eventbus import EventBus
from ...servicios.energia.power import (
    ACTION_LOCK,
    ACTION_LOGOUT,
    ACTION_REBOOT,
    ACTION_SHUTDOWN,
    ACTION_SUSPEND,
    PowerService,
)
from ...popup_handle import PopupHandle, PopupOutsideDismiss, pointer_inside_widget, present_popup, hide_popup
from ...ui import ShellModule
from ...window_identity import (
    TITLE_POWER_CONFIRM,
    TITLE_POWER_MENU,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)

MenuEntry = tuple[str, str, str, bool]

POWER_MENU_ENTRIES: tuple[MenuEntry, ...] = (
    (ACTION_LOCK, "Bloquear", "system-lock-screen-symbolic", False),
    (ACTION_SUSPEND, "Suspender", "system-suspend-symbolic", False),
    (ACTION_LOGOUT, "Cerrar sesión", "system-log-out-symbolic", True),
    (ACTION_REBOOT, "Reiniciar", "system-reboot-symbolic", True),
    (ACTION_SHUTDOWN, "Apagar", "system-shutdown-symbolic", True),
)

_CONFIRM_COPY = {
    ACTION_LOGOUT: ("¿Cerrar sesión?", "Cerrar sesión"),
    ACTION_REBOOT: ("¿Reiniciar el equipo?", "Reiniciar"),
    ACTION_SHUTDOWN: ("¿Apagar el equipo?", "Apagar"),
}

POWER_CONTROL_CENTER_REQUESTED = "power_control_center_requested"


class PowerMenu(Gtk.Window):
    """Compact action list anchored below the power button."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        on_action_selected: Callable[[str], None],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._on_action_selected = on_action_selected
        self._anchor_button: Gtk.Widget | None = None

        self.set_name("shell-power-menu")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_POWER_MENU)
        configure_interactive_popup(self)

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._box.get_style_context().add_class("power-menu-content")
        self.add(self._box)

        for action, label, icon_name, destructive in POWER_MENU_ENTRIES:
            self._box.pack_start(
                self._make_row(action, label, icon_name, destructive),
                False,
                False,
                0,
            )

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_menu(self) -> None:
        self._anchor_button = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

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
        button.connect("clicked", lambda _btn, act=action: self._on_action_selected(act))
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


class PowerConfirmDialog(Gtk.Window):
    """Small confirmation panel for destructive session actions."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        on_confirmed: Callable[[], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._on_confirmed = on_confirmed
        self._on_cancelled = on_cancelled
        self._anchor_widget: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None

        self.set_name("shell-power-confirm")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_POWER_CONFIRM)
        configure_interactive_popup(self)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.get_style_context().add_class("power-confirm-content")
        self.add(outer)

        self._message = Gtk.Label(xalign=0.5)
        self._message.get_style_context().add_class("power-confirm-message")
        outer.pack_start(self._message, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        outer.pack_start(actions, False, False, 0)

        cancel = Gtk.Button(label="Cancelar")
        cancel.get_style_context().add_class("power-confirm-cancel")
        cancel.connect("clicked", self._handle_cancel)
        actions.pack_start(cancel, False, False, 0)

        self._confirm_button = Gtk.Button()
        self._confirm_button.get_style_context().add_class("power-confirm-action")
        self._confirm_button.connect("clicked", self._handle_confirm)
        actions.pack_start(self._confirm_button, False, False, 0)

    def open_for(
        self,
        anchor_widget: Gtk.Widget,
        message: str,
        confirm_label: str,
        *,
        destructive: bool,
    ) -> None:
        self._anchor_widget = anchor_widget
        self._fixed_popup_top = None
        self._message.set_text(message)
        self._confirm_button.set_label(confirm_label)
        style = self._confirm_button.get_style_context()
        if destructive:
            style.add_class("power-confirm-action-destructive")
        else:
            style.remove_class("power-confirm-action-destructive")
        present_popup(self)
        schedule_popup_position(self._position_after_show)

    def close_dialog(self) -> None:
        self._anchor_widget = None
        self._fixed_popup_top = None
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def _handle_cancel(self, *_args) -> None:
        self._on_cancelled()

    def _handle_confirm(self, *_args) -> None:
        self._on_confirmed()

    def _position_after_show(self) -> bool:
        if self._anchor_widget is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor_widget,
                title=TITLE_POWER_CONFIRM,
                offset=POWER_MENU_OFFSET,
                fixed_top=self._fixed_popup_top,
            )
            if self._fixed_popup_top is None and top is not None:
                self._fixed_popup_top = top
        return False


class PowerWidget(ShellModule):
    """Power button embedded in the main bar; menu and confirmations are separate windows."""

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
        self._shell_press_bound = False
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
        self.pack_start(self._button, False, False, 0)

        self._menu = PopupHandle(self._create_menu)
        self._confirm = PopupHandle(self._create_confirm)
        self._outside_click = PopupOutsideDismiss()

    def apply_shell_compact(self, compact: bool) -> None:
        self._icon.set_pixel_size(
            POWER_COMPACT_ICON_SIZE if compact else POWER_ICON_SIZE
        )

    def get_anchor_button(self) -> Gtk.Widget:
        return self._button

    def _create_menu(self) -> PowerMenu:
        return PowerMenu(self._shell_window, self._on_menu_action_selected)

    def _create_confirm(self) -> PowerConfirmDialog:
        return PowerConfirmDialog(
            self._shell_window,
            self._on_confirm_accepted,
            self._on_confirm_cancelled,
        )

    def _ensure_shell_press_handler(self) -> None:
        if self._shell_press_bound:
            return
        self._shell_window.connect("button-press-event", self._on_shell_button_press)
        self._shell_press_bound = True

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            self._event_bus.emit(POWER_CONTROL_CENTER_REQUESTED, self._button)
            return True
        if event.button == 1:
            self._toggle_power_menu()
            return True
        return False

    def _toggle_power_menu(self) -> None:
        if self._menu.is_visible():
            self.close_menu()
            return
        self._ensure_shell_press_handler()
        menu = self._menu.get()
        menu.open_for(self._button)
        self._outside_click.install(
            menu,
            self._shell_window,
            (self._button,),
            self.close_menu,
            self._shell_window.event_bus,
        )

    def _on_menu_action_selected(self, action: str) -> None:
        if action in _CONFIRM_COPY:
            message, confirm_label = _CONFIRM_COPY[action]
            self._pending_action = action
            menu = self._menu.maybe
            if menu is not None:
                menu.close_menu()
            confirm = self._confirm.get()
            confirm.open_for(
                self._button,
                message,
                confirm_label,
                destructive=True,
            )
            self._outside_click.install(
                confirm,
                self._shell_window,
                (self._button,),
                self._on_confirm_cancelled,
                self._shell_window.event_bus,
            )
            return

        self._execute_action(action)
        self.close_menu()

    def _on_confirm_accepted(self) -> None:
        action = self._pending_action
        self._pending_action = None
        confirm = self._confirm.maybe
        if confirm is not None:
            confirm.close_dialog()
        if action is not None:
            self._execute_action(action)
        self.close_menu()

    def _on_confirm_cancelled(self) -> None:
        self._pending_action = None
        confirm = self._confirm.maybe
        if confirm is not None:
            confirm.close_dialog()
        self.close_menu()

    def _execute_action(self, action: str) -> None:
        handler = {
            ACTION_LOCK: self._power_service.lock,
            ACTION_SUSPEND: self._power_service.suspend,
            ACTION_LOGOUT: self._power_service.logout,
            ACTION_REBOOT: self._power_service.reboot,
            ACTION_SHUTDOWN: self._power_service.shutdown,
        }.get(action)
        if handler is None:
            return
        try:
            handler()
        except Exception as error:
            print(f"shell: power action {action} failed: {error}")

    def close_menu(self) -> None:
        self._outside_click.uninstall()
        menu = self._menu.maybe
        if menu is not None:
            menu.close_menu()
        confirm = self._confirm.maybe
        if confirm is not None:
            confirm.close_dialog()
        self._pending_action = None

    def _on_shell_button_press(self, _window: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button not in (1, 3):
            return False

        confirm = self._confirm.maybe
        if confirm is not None and confirm.get_visible():
            if confirm.pointer_is_inside():
                return False
            self._on_confirm_cancelled()
            return False

        if not self._menu.is_visible():
            return False

        menu = self._menu.maybe
        if menu is None:
            return False

        if pointer_inside_widget(self._button) or menu.pointer_is_inside():
            return False

        self.close_menu()
        return False


def _pointer_inside_widget(widget: Gtk.Widget) -> bool:
    return pointer_inside_widget(widget)
