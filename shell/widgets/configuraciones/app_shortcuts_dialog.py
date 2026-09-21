"""Application-shortcut manager as a layer-shell overlay.

Closes Configuraciones first so it does not sit underneath, and avoids modal
``Gtk.Dialog`` grabs that block the bar after dismiss.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from ...identity import APPLICATION_NAME
from ...models import DesktopApplication
from ...popup_handle import hide_popup, present_popup
from ...servicios.aplicaciones.desktop import scan_desktop_applications
from ...servicios.atajos.hypr_sync import reload_hyprland
from ...servicios.atajos.keys import format_conf_keys
from ...servicios.atajos.manager import (
    UserAppShortcutError,
    add_user_app_shortcut,
    list_user_app_shortcuts,
    remove_user_app_shortcut,
    update_user_app_shortcut,
)
from ...servicios.atajos.user_apps import (
    UserAppShortcut,
    normalize_hypr_key,
    normalize_mods,
)
from ...window_identity import (
    configure_interactive_popup,
    configure_toplevel,
    register_shell_popup,
)

_CARD_WIDTH = 560
_CARD_HEIGHT = 520
_TITLE = f"{APPLICATION_NAME} Atajos de aplicaciones"

_active: "_AppShortcutsOverlay | None" = None


def open_app_shortcuts_manager(
    parent: Gtk.Widget | None = None,
    *,
    on_changed: Callable[[], None] | None = None,
) -> None:
    """Close settings/launcher, then open the manager overlay."""
    global _active
    if _active is not None and _active.get_realized():
        _active.present()
        return

    shell_parent = _close_settings_host(parent)

    def show_manager() -> bool:
        global _active
        overlay = _AppShortcutsOverlay(
            shell_parent=shell_parent,
            on_changed=on_changed,
        )
        _active = overlay
        overlay.connect("destroy", _on_overlay_destroyed)
        present_popup(overlay)
        return False

    # Let the settings puerta release EXCLUSIVE before we take keyboard.
    GLib.timeout_add(80, show_manager)


def _on_overlay_destroyed(_widget: Gtk.Widget) -> None:
    global _active
    _active = None


def _close_settings_host(parent: Gtk.Widget | None) -> Gtk.Window | None:
    """Hide Configuraciones / launcher so it cannot sit above the bar."""
    if parent is None:
        return None
    toplevel = parent.get_toplevel()
    if not isinstance(toplevel, Gtk.Window):
        return None
    # Drop EXCLUSIVE keyboard immediately — door close alone leaves the bar dead.
    if GtkLayerShell.is_layer_window(toplevel):
        GtkLayerShell.set_keyboard_mode(toplevel, GtkLayerShell.KeyboardMode.NONE)
    if hasattr(toplevel, "close_picker"):
        toplevel.close_picker()
    elif hasattr(toplevel, "close_settings"):
        toplevel.close_settings()
    # Force-hide so Configuraciones cannot remain on top of the manager / bar.
    if toplevel.get_visible():
        toplevel.hide()
    return toplevel


class _AppShortcutsOverlay(Gtk.Window):
    """Fullscreen layer-shell card: list + inline editor (no nested dialogs)."""

    def __init__(
        self,
        *,
        shell_parent: Gtk.Window | None,
        on_changed: Callable[[], None] | None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._on_changed = on_changed
        self._closing = False
        self._dirty = False
        self._apps: tuple[DesktopApplication, ...] = ()
        self._apps_by_id: dict[str, DesktopApplication] = {}
        self._editing: UserAppShortcut | None = None
        self._selected_id = ""
        self._captured_mods: tuple[str, ...] = ("SUPER",)
        self._captured_key = ""
        self._app_rows: list[tuple[str, str, Gtk.ListBoxRow]] = []

        self.set_name("shell-app-shortcuts")
        self.get_style_context().add_class("shell-picker")
        self.get_style_context().add_class("shell-settings")
        if shell_parent is not None:
            register_shell_popup(self, shell_parent)
        configure_toplevel(self, title=_TITLE)
        configure_interactive_popup(self)
        self.set_default_size(_CARD_WIDTH, _CARD_HEIGHT)
        self._configure_layer_shell()

        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("launcher-backdrop")
        backdrop.connect("button-press-event", self._on_backdrop)
        self.add(backdrop)

        aligner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        aligner.set_halign(Gtk.Align.CENTER)
        aligner.set_valign(Gtk.Align.CENTER)
        backdrop.add(aligner)

        card = Gtk.EventBox()
        card.get_style_context().add_class("launcher-card-host")
        card.connect("button-press-event", lambda _w, event: event.button == 1)
        aligner.pack_start(card, False, False, 0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.get_style_context().add_class("launcher-card")
        outer.get_style_context().add_class("settings-card")
        outer.set_size_request(_CARD_WIDTH, _CARD_HEIGHT)
        card.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._title = Gtk.Label(label="Atajos de aplicaciones", xalign=0)
        self._title.get_style_context().add_class("settings-header-title")
        self._title.set_hexpand(True)
        header.pack_start(self._title, True, True, 0)
        close_btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        close_btn.get_style_context().add_class("settings-close")
        close_btn.set_tooltip_text("Cerrar")
        close_btn.add(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        )
        close_btn.connect("clicked", lambda *_: self.close_manager())
        header.pack_end(close_btn, False, False, 0)
        outer.pack_start(header, False, False, 0)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        outer.pack_start(self._stack, True, True, 0)

        self._build_list_page()
        self._build_editor_page()
        self._stack.set_visible_child_name("list")

        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._on_key_press)
        self.connect("hide", self._on_hide)

        threading.Thread(target=self._load_apps_worker, daemon=True).start()
        self._refresh_list()

    def close_manager(self) -> None:
        if self._closing:
            return
        self._closing = True
        if GtkLayerShell.is_layer_window(self):
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        hide_popup(self)
        # Destroy after fade so the overlay surface is fully gone.
        GLib.timeout_add(220, self._destroy_later)

    def _destroy_later(self) -> bool:
        if self.get_realized():
            self.destroy()
        if self._dirty:
            threading.Thread(target=reload_hyprland, daemon=True).start()
            if self._on_changed is not None:
                try:
                    self._on_changed()
                except Exception:  # noqa: BLE001
                    pass
        return False

    def _on_hide(self, _widget: Gtk.Widget) -> None:
        # Ensure layer-shell keyboard grab is cleared when the surface hides.
        if GtkLayerShell.is_layer_window(self):
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

    def _configure_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "shell-app-shortcuts")
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

    def _build_list_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_border_width(4)

        self._status = Gtk.Label(xalign=0)
        self._status.get_style_context().add_class("settings-page-note")
        self._status.set_line_wrap(True)
        self._status.set_text("Cargando aplicaciones…")
        page.pack_start(self._status, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._list_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.add(self._list_host)
        page.pack_start(scroll, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._add_btn = Gtk.Button(label="Añadir atajo")
        self._add_btn.get_style_context().add_class("settings-browse-button")
        self._add_btn.set_sensitive(False)
        self._add_btn.connect("clicked", lambda *_: self._open_editor(None))
        actions.pack_start(self._add_btn, False, False, 0)
        page.pack_start(actions, False, False, 0)

        self._stack.add_named(page, "list")

    def _build_editor_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_border_width(4)

        page.pack_start(Gtk.Label(label="Aplicación", xalign=0), False, False, 0)
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Buscar aplicación…")
        self._search.connect("search-changed", self._on_search)
        page.pack_start(self._search, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        scrolled.set_vexpand(True)
        self._app_list = Gtk.ListBox()
        self._app_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._app_list.set_activate_on_single_click(True)
        self._app_list.connect("row-activated", self._on_row_activated)
        self._app_list.connect("row-selected", self._on_row_selected)
        scrolled.add(self._app_list)
        page.pack_start(scrolled, True, True, 0)

        self._selected_label = Gtk.Label(xalign=0)
        self._selected_label.get_style_context().add_class("settings-page-note")
        page.pack_start(self._selected_label, False, False, 0)

        page.pack_start(
            Gtk.Label(label="Combinación (haz clic y pulsa las teclas)", xalign=0),
            False,
            False,
            0,
        )
        self._key_entry = Gtk.Entry()
        self._key_entry.set_editable(False)
        self._key_entry.set_placeholder_text("Ej.: SUPER + B")
        self._key_entry.connect("key-press-event", self._on_capture_key)
        page.pack_start(self._key_entry, False, False, 0)

        hint = Gtk.Label(
            label="Obligatorio SUPER. Reservadas: J/K, F, 1–9, Q, W, Space, Enter, E…",
            xalign=0,
        )
        hint.get_style_context().add_class("settings-page-note")
        hint.set_line_wrap(True)
        page.pack_start(hint, False, False, 0)

        self._error = Gtk.Label(xalign=0)
        self._error.get_style_context().add_class("settings-row-hint")
        page.pack_start(self._error, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancelar")
        cancel.get_style_context().add_class("settings-browse-button")
        cancel.connect("clicked", lambda *_: self._show_list())
        save = Gtk.Button(label="Guardar")
        save.get_style_context().add_class("settings-browse-button")
        save.connect("clicked", lambda *_: self._save_editor())
        buttons.pack_start(cancel, False, False, 0)
        buttons.pack_start(save, False, False, 0)
        page.pack_start(buttons, False, False, 0)

        self._stack.add_named(page, "editor")

    def _load_apps_worker(self) -> None:
        try:
            apps = tuple(
                sorted(scan_desktop_applications(), key=lambda item: item.name.casefold())
            )
        except Exception:  # noqa: BLE001
            apps = ()
        GLib.idle_add(self._on_apps_ready, apps)

    def _on_apps_ready(self, apps: tuple[DesktopApplication, ...]) -> bool:
        if self._closing:
            return False
        self._apps = apps
        self._apps_by_id = {app.id: app for app in apps}
        self._add_btn.set_sensitive(True)
        self._refresh_list()
        return False

    def _refresh_list(self) -> None:
        for child in list(self._list_host.get_children()):
            self._list_host.remove(child)
            child.destroy()
        shortcuts = list_user_app_shortcuts()
        if not shortcuts:
            empty = Gtk.Label(
                label="No hay atajos de aplicación. Pulsa «Añadir atajo».",
                xalign=0,
            )
            empty.get_style_context().add_class("settings-page-note")
            self._list_host.pack_start(empty, False, False, 0)
        else:
            for item in shortcuts:
                self._list_host.pack_start(self._managed_row(item), False, False, 0)
        self._list_host.show_all()
        self._status.set_text(
            f"{len(shortcuts)} atajo(s) · {len(self._apps)} aplicaciones disponibles. "
            "Teclas del sistema (J/K, F, 1–9, …) protegidas."
        )

    def _managed_row(self, shortcut: UserAppShortcut) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("settings-row")

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        keys = Gtk.Label(label=shortcut.keys_display, xalign=0)
        keys.get_style_context().add_class("settings-shortcut-keys")
        texts.pack_start(keys, False, False, 0)
        name = Gtk.Label(label=shortcut.app_name, xalign=0)
        name.get_style_context().add_class("settings-row-title")
        texts.pack_start(name, False, False, 0)
        row.pack_start(texts, True, True, 0)

        edit_btn = Gtk.Button(label="Editar")
        edit_btn.get_style_context().add_class("settings-browse-button")
        edit_btn.connect("clicked", lambda *_: self._open_editor(shortcut))
        row.pack_end(edit_btn, False, False, 0)

        del_btn = Gtk.Button(label="Eliminar")
        del_btn.get_style_context().add_class("settings-browse-button")
        del_btn.connect("clicked", lambda *_: self._delete_shortcut(shortcut))
        row.pack_end(del_btn, False, False, 0)
        return row

    def _delete_shortcut(self, shortcut: UserAppShortcut) -> None:
        remove_user_app_shortcut(shortcut.id, reload=False)
        self._dirty = True
        self._refresh_list()
        self._status.set_text("Atajo eliminado (se aplicará al cerrar).")

    def _open_editor(self, existing: UserAppShortcut | None) -> None:
        if not self._apps:
            self._status.set_text("Todavía no hay aplicaciones cargadas.")
            return
        self._editing = existing
        self._selected_id = (
            existing.app_id if existing is not None else self._apps[0].id
        )
        self._captured_mods = existing.mods if existing is not None else ("SUPER",)
        self._captured_key = existing.key if existing is not None else ""
        self._key_entry.set_text(
            existing.keys_display if existing is not None else ""
        )
        self._error.set_text("")
        self._title.set_text(
            "Editar atajo" if existing is not None else "Nuevo atajo de aplicación"
        )
        self._search.set_text("")
        self._populate_apps("")
        self._update_selected_label()
        self._stack.set_visible_child_name("editor")
        GLib.idle_add(self._focus_search)

    def _show_list(self) -> None:
        self._title.set_text("Atajos de aplicaciones")
        self._stack.set_visible_child_name("list")
        self._refresh_list()

    def _focus_search(self) -> bool:
        self._search.grab_focus()
        return False

    def _populate_apps(self, query: str) -> None:
        for child in list(self._app_list.get_children()):
            self._app_list.remove(child)
            child.destroy()
        self._app_rows.clear()
        needle = query.strip().casefold()
        active_row: Gtk.ListBoxRow | None = None
        for app in self._apps:
            hay = f"{app.name} {app.id} {app.comment}".casefold()
            if needle and needle not in hay:
                continue
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=app.name, xalign=0)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            row.add(label)
            self._app_list.add(row)
            self._app_rows.append((app.id, app.name, row))
            if app.id == self._selected_id:
                active_row = row
        self._app_list.show_all()
        if active_row is not None:
            self._app_list.select_row(active_row)
        elif self._app_rows:
            self._app_list.select_row(self._app_rows[0][2])
            self._selected_id = self._app_rows[0][0]
            self._update_selected_label()

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._populate_apps(entry.get_text() or "")

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._select_row(row)

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is not None:
            self._select_row(row)

    def _select_row(self, row: Gtk.ListBoxRow) -> None:
        for app_id, _name, item in self._app_rows:
            if item is row:
                self._selected_id = app_id
                self._update_selected_label()
                return

    def _update_selected_label(self) -> None:
        app = self._apps_by_id.get(self._selected_id)
        if app is None:
            self._selected_label.set_text("Ninguna aplicación seleccionada.")
        else:
            self._selected_label.set_text(f"Seleccionada: {app.name}")

    def _on_capture_key(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key_name = Gdk.keyval_name(event.keyval) or ""
        if key_name in {"Escape", "Esc"}:
            return False
        if key_name in {
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
            "Super_L",
            "Super_R",
            "Meta_L",
            "Meta_R",
            "ISO_Level3_Shift",
            "Caps_Lock",
        }:
            return True
        mods: list[str] = []
        state = int(event.state)
        super_mask = int(Gdk.ModifierType.SUPER_MASK)
        mod4_mask = int(getattr(Gdk.ModifierType, "MOD4_MASK", 0))
        if state & super_mask or (mod4_mask and state & mod4_mask):
            mods.append("SUPER")
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods.append("CTRL")
        if state & Gdk.ModifierType.MOD1_MASK:
            mods.append("ALT")
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods.append("SHIFT")
        if "SUPER" not in mods:
            mods.insert(0, "SUPER")
        hypr_key = _gdk_key_to_hypr(key_name)
        if not hypr_key:
            return True
        self._captured_mods = normalize_mods(tuple(mods))
        self._captured_key = normalize_hypr_key(hypr_key)
        self._key_entry.set_text(
            format_conf_keys(" ".join(self._captured_mods), self._captured_key)
        )
        return True

    def _save_editor(self) -> None:
        app = self._apps_by_id.get(self._selected_id)
        if app is None:
            self._error.set_text("Selecciona una aplicación de la lista.")
            return
        if not self._captured_key:
            self._error.set_text("Pulsa una combinación de teclas.")
            return
        try:
            if self._editing is None:
                add_user_app_shortcut(
                    app_id=app.id,
                    app_name=app.name,
                    mods=self._captured_mods,
                    key=self._captured_key,
                    reload=False,
                )
            else:
                update_user_app_shortcut(
                    self._editing.id,
                    app_id=app.id,
                    app_name=app.name,
                    mods=self._captured_mods,
                    key=self._captured_key,
                    reload=False,
                )
        except UserAppShortcutError as error:
            self._error.set_text(str(error))
            return
        self._dirty = True
        self._show_list()

    def _on_backdrop(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.close_manager()
        return True

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key_name = Gdk.keyval_name(event.keyval) or ""
        if key_name not in {"Escape", "Esc"}:
            return False
        if self._stack.get_visible_child_name() == "editor":
            self._show_list()
            return True
        self.close_manager()
        return True


def _gdk_key_to_hypr(key_name: str) -> str:
    mapping = {
        "Return": "Return",
        "KP_Enter": "Return",
        "space": "Space",
        "Tab": "Tab",
        "period": "period",
        "comma": "comma",
    }
    if key_name in mapping:
        return mapping[key_name]
    if len(key_name) == 1:
        return key_name.upper()
    if key_name.startswith("F") and key_name[1:].isdigit():
        return key_name
    if key_name.isalpha() and len(key_name) <= 2:
        return key_name.upper()
    return ""
