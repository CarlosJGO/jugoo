"""Hotspot / access-point controls for the control center network section."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...models import HotspotSnapshot, NetworkSnapshot
from ...servicios.red.network_hotspot import (
    HOTSPOT_BAND_24,
    HOTSPOT_BAND_5,
    HOTSPOT_BAND_AUTO,
    hotspot_status_label,
)
from ...ui import ShellModule

_BAND_ROWS = (
    (HOTSPOT_BAND_AUTO, "Automática"),
    (HOTSPOT_BAND_24, "2.4 GHz"),
    (HOTSPOT_BAND_5, "5 GHz"),
)


class ControlCenterHotspotSection(ShellModule):
    """Hotspot UI driven only by HotspotSnapshot + NetworkService callbacks."""

    def __init__(
        self,
        *,
        on_toggle_hotspot: Callable[[bool, str, str, str], None],
        on_apply_hotspot: Callable[[str, str, str], None],
    ) -> None:
        super().__init__("control-center-hotspot-section", spacing=0)
        self._on_toggle_hotspot = on_toggle_hotspot
        self._on_apply_hotspot = on_apply_hotspot
        self._syncing = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("control-center-subsection")
        self.pack_start(box, True, True, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label="Punto de acceso", xalign=0)
        heading.get_style_context().add_class("control-center-subsection-title")
        heading.set_hexpand(True)
        header.pack_start(heading, True, True, 0)
        self._switch = Gtk.Switch()
        self._switch.set_halign(Gtk.Align.END)
        self._switch.connect("notify::active", self._on_switch_changed)
        header.pack_start(self._switch, False, False, 0)
        box.pack_start(header, False, False, 0)

        self._status = Gtk.Label(xalign=0)
        self._status.get_style_context().add_class("control-center-detail")
        self._status.set_line_wrap(True)
        box.pack_start(self._status, False, False, 0)

        self._ssid_entry = Gtk.Entry()
        self._ssid_entry.set_placeholder_text("Nombre de la red")
        box.pack_start(self._labeled("Nombre", self._ssid_entry), False, False, 0)

        password_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._password_entry = Gtk.Entry()
        self._password_entry.set_visibility(False)
        self._password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self._password_entry.set_placeholder_text("Mínimo 8 caracteres")
        self._password_entry.set_hexpand(True)
        password_row.pack_start(self._password_entry, True, True, 0)
        self._password_reveal = Gtk.Button(label="Mostrar", relief=Gtk.ReliefStyle.NONE)
        self._password_reveal.get_style_context().add_class("control-center-action")
        self._password_reveal.connect("clicked", self._on_toggle_password_visibility)
        password_row.pack_start(self._password_reveal, False, False, 0)
        box.pack_start(self._labeled("Contraseña", password_row), False, False, 0)

        self._band_store = Gtk.ListStore(str, str, bool)
        self._band_combo = Gtk.ComboBox.new_with_model(self._band_store)
        renderer = Gtk.CellRendererText()
        self._band_combo.pack_start(renderer, True)
        self._band_combo.add_attribute(renderer, "text", 1)
        self._band_combo.add_attribute(renderer, "sensitive", 2)
        self._band_hint = Gtk.Label(xalign=0)
        self._band_hint.get_style_context().add_class("control-center-detail-muted")
        self._band_hint.set_line_wrap(True)
        band_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        band_box.pack_start(self._band_combo, False, False, 0)
        band_box.pack_start(self._band_hint, False, False, 0)
        box.pack_start(self._labeled("Banda", band_box), False, False, 0)

        apply_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        apply_row.set_halign(Gtk.Align.END)
        apply_button = Gtk.Button(label="Guardar", relief=Gtk.ReliefStyle.NONE)
        apply_button.get_style_context().add_class("control-center-action")
        apply_button.connect("clicked", self._on_apply_clicked)
        apply_row.pack_start(apply_button, False, False, 0)
        box.pack_start(apply_row, False, False, 0)

        share_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        share_title = Gtk.Label(label="Ethernet → Wi-Fi", xalign=0)
        share_title.get_style_context().add_class("control-center-detail")
        share_title.set_hexpand(True)
        share_row.pack_start(share_title, True, True, 0)
        box.pack_start(share_row, False, False, 0)

        self._sharing = Gtk.Label(xalign=0)
        self._sharing.get_style_context().add_class("control-center-hotspot-sharing")
        self._sharing.set_line_wrap(True)
        box.pack_start(self._sharing, False, False, 0)

        self._clients = Gtk.Label(xalign=0)
        self._clients.get_style_context().add_class("control-center-detail-muted")
        box.pack_start(self._clients, False, False, 0)

    def refresh(self, snapshot: NetworkSnapshot) -> None:
        hotspot = snapshot.hotspot
        self._syncing = True
        try:
            self._sync_band_options(hotspot)
            controls_sensitive = hotspot.available and hotspot.supports_ap
            self._switch.set_sensitive(controls_sensitive)
            self._switch.set_active(hotspot.active or hotspot.status == "starting")
            self._ssid_entry.set_sensitive(controls_sensitive)
            self._password_entry.set_sensitive(controls_sensitive)
            self._band_combo.set_sensitive(controls_sensitive)
            if not self._ssid_entry.has_focus():
                self._ssid_entry.set_text(hotspot.ssid)
            if hotspot.password_configured:
                self._password_entry.set_placeholder_text("Dejar vacío para no cambiarla")
            else:
                self._password_entry.set_placeholder_text("Mínimo 8 caracteres")
            status = hotspot_status_label(hotspot)
            if hotspot.error_message and hotspot.status in {"auth_error", "config_error"}:
                status = f"{status}: {hotspot.error_message}"
            self._status.set_text(status)
            if hotspot.shared_connection:
                self._sharing.set_text("● Compartiendo Internet")
            elif hotspot.active and hotspot.ipv4_shared and not hotspot.ethernet_upstream:
                self._sharing.set_text("○ Sin Ethernet de origen")
            elif hotspot.active and hotspot.ipv4_shared and not hotspot.forwarding_enabled:
                self._sharing.set_text("○ El sistema no permite reenviar tráfico")
            elif hotspot.active:
                self._sharing.set_text("○ Punto de acceso activo")
            else:
                self._sharing.set_text("○ No se está compartiendo Internet")
            count = len(hotspot.connected_clients)
            if not hotspot.active:
                self._clients.set_text("")
            elif count == 1:
                self._clients.set_text("1 dispositivo conectado")
            else:
                self._clients.set_text(f"{count} dispositivos conectados")
        finally:
            self._syncing = False

    def _sync_band_options(self, hotspot: HotspotSnapshot) -> None:
        selected = self._selected_band() if self._band_store.iter_n_children(None) else hotspot.band
        self._band_store.clear()
        for band_id, label in _BAND_ROWS:
            sensitive = True
            if band_id == HOTSPOT_BAND_5 and not hotspot.supports_5ghz:
                sensitive = False
            if band_id == HOTSPOT_BAND_24 and not hotspot.supports_2_4ghz and hotspot.available:
                sensitive = hotspot.supports_5ghz
            self._band_store.append([band_id, label, sensitive])
        target = hotspot.band if hotspot.band in {row[0] for row in _BAND_ROWS} else HOTSPOT_BAND_AUTO
        if not self._band_combo.has_focus():
            self._select_band(target)
        elif selected:
            self._select_band(selected)
        if hotspot.available and not hotspot.supports_5ghz:
            self._band_hint.set_text(
                "5 GHz no está disponible en este adaptador (NetworkManager).",
            )
        else:
            self._band_hint.set_text("")

    def _labeled(self, title: str, child: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        label = Gtk.Label(label=title, xalign=0)
        label.get_style_context().add_class("control-center-detail-muted")
        box.pack_start(label, False, False, 0)
        box.pack_start(child, False, False, 0)
        return box

    def _selected_band(self) -> str:
        iterator = self._band_combo.get_active_iter()
        if iterator is None:
            return HOTSPOT_BAND_AUTO
        value = self._band_store.get_value(iterator, 0)
        return str(value)

    def _select_band(self, band: str) -> None:
        iterator = self._band_store.get_iter_first()
        while iterator is not None:
            if self._band_store.get_value(iterator, 0) == band:
                if self._band_store.get_value(iterator, 2):
                    self._band_combo.set_active_iter(iterator)
                    return
            iterator = self._band_store.iter_next(iterator)
        fallback = self._band_store.get_iter_first()
        if fallback is not None:
            self._band_combo.set_active_iter(fallback)

    def _on_toggle_password_visibility(self, *_args) -> None:
        visible = not self._password_entry.get_visibility()
        self._password_entry.set_visibility(visible)
        self._password_reveal.set_label("Ocultar" if visible else "Mostrar")

    def _on_switch_changed(self, switch: Gtk.Switch, _pspec) -> None:
        if self._syncing:
            return
        self._on_toggle_hotspot(
            switch.get_active(),
            self._ssid_entry.get_text(),
            self._password_entry.get_text(),
            self._selected_band(),
        )

    def _on_apply_clicked(self, *_args) -> None:
        self._on_apply_hotspot(
            self._ssid_entry.get_text(),
            self._password_entry.get_text(),
            self._selected_band(),
        )
