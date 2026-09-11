"""Settings Center overlay — same GtkLayerShell chrome as Search/Clipboard/Emoji."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ...config import SETTINGS_CARD_HEIGHT, SETTINGS_CARD_WIDTH
from ...identity import TITLE_SETTINGS
from ...popup_handle import hide_popup, present_popup
from ...settings.manager import SettingsManager
from ...settings.schema import CATEGORY_META, CategoryId
from ...ui.starfield import install_starfield, resolve_event_bus
from ...window_identity import configure_interactive_popup, configure_toplevel, register_shell_popup
from .pages import build_category_page


class SettingsOverlay(Gtk.Window):
    """Fullscreen exclusive overlay with Jugoo picker card aesthetics."""

    def __init__(self, shell_window: Gtk.Window, manager: SettingsManager) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._manager = manager
        self._closing = False
        self._nav_buttons: dict[CategoryId, Gtk.Button] = {}
        self._active_category = CategoryId.GENERAL
        self._content_host: Gtk.Box | None = None

        self.set_name("shell-settings")
        self.get_style_context().add_class("shell-picker")
        self.get_style_context().add_class("shell-settings")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_SETTINGS)
        configure_interactive_popup(self)
        self.set_default_size(SETTINGS_CARD_WIDTH, SETTINGS_CARD_HEIGHT)
        self._configure_layer_shell()

        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("launcher-backdrop")
        backdrop.connect("button-press-event", self._on_backdrop_press)
        self.add(backdrop)

        aligner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        aligner.set_halign(Gtk.Align.CENTER)
        aligner.set_valign(Gtk.Align.CENTER)
        backdrop.add(aligner)

        card = Gtk.EventBox()
        card.get_style_context().add_class("launcher-card-host")
        card.connect("button-press-event", self._on_card_press)
        aligner.pack_start(card, False, False, 0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.get_style_context().add_class("launcher-card")
        outer.get_style_context().add_class("settings-card")
        outer.set_size_request(SETTINGS_CARD_WIDTH, SETTINGS_CARD_HEIGHT)
        install_starfield(
            card,
            outer,
            resolve_event_bus(shell_window),
            corner_radius=16.0,
        )

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.get_style_context().add_class("settings-header")
        title = Gtk.Label(label="Configuraciones", xalign=0)
        title.get_style_context().add_class("settings-header-title")
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)
        close_btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        close_btn.get_style_context().add_class("settings-close")
        close_btn.set_tooltip_text("Cerrar")
        close_icon = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close_btn.add(close_icon)
        close_btn.connect("clicked", lambda *_: self.close_settings())
        header.pack_end(close_btn, False, False, 0)
        outer.pack_start(header, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.get_style_context().add_class("settings-body")
        outer.pack_start(body, True, True, 0)

        nav_scroll = Gtk.ScrolledWindow()
        nav_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        nav_scroll.set_size_request(168, -1)
        nav_scroll.get_style_context().add_class("settings-nav-scroll")
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        nav.get_style_context().add_class("settings-nav")
        nav_scroll.add(nav)
        body.pack_start(nav_scroll, False, False, 0)

        ordered = (CategoryId.GENERAL,) + tuple(
            cat for cat in manager.categories_present() if cat is not CategoryId.GENERAL
        )
        for category in ordered:
            label, _subtitle = CATEGORY_META[category]
            button = Gtk.Button(label=label, relief=Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("settings-nav-button")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", self._on_nav_clicked, category)
            nav.pack_start(button, False, False, 0)
            self._nav_buttons[category] = button

        content_scroll = Gtk.ScrolledWindow()
        content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_scroll.set_hexpand(True)
        content_scroll.get_style_context().add_class("settings-content-scroll")
        self._content_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content_host.get_style_context().add_class("settings-content")
        content_scroll.add(self._content_host)
        body.pack_start(content_scroll, True, True, 0)

        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self._show_category(CategoryId.GENERAL)

    def open_settings(self) -> None:
        self._closing = False
        self._show_category(self._active_category)
        present_popup(self)
        GLib.idle_add(self._focus_self)

    def close_settings(self) -> None:
        if self._closing or not self.get_visible():
            hide_popup(self)
            return
        self._closing = True
        hide_popup(self)

    def toggle_settings(self) -> None:
        if self.get_visible():
            self.close_settings()
        else:
            self.open_settings()

    def _focus_self(self) -> bool:
        self.grab_focus()
        return False

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-settings")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)

    def _on_nav_clicked(self, _button: Gtk.Button, category: CategoryId) -> None:
        self._show_category(category)

    def _show_category(self, category: CategoryId) -> None:
        self._active_category = category
        for cat, button in self._nav_buttons.items():
            ctx = button.get_style_context()
            if cat is category:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

        assert self._content_host is not None
        for child in list(self._content_host.get_children()):
            self._content_host.remove(child)
            child.destroy()

        page = build_category_page(
            self._manager,
            category,
            on_change=self._on_setting_changed,
        )
        self._content_host.pack_start(page, True, True, 0)
        self._content_host.show_all()

    def _on_setting_changed(self, key: str, value: object) -> None:
        self._manager.set(key, value)
        # Rebuild night / layout pages so banners and previews stay honest.
        if key.startswith("modo_noche.") or key.startswith("layout."):
            self._show_category(self._active_category)
        elif key == "tema.active":
            # Theme CSS refresh is enough; keep page mounted.
            pass

    def _on_backdrop_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.close_settings()
        return True

    def _on_card_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        return event.button == 1

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key_name = Gdk.keyval_name(event.keyval) or ""
        if key_name in {"Escape", "Esc"}:
            self.close_settings()
            return True
        return False
