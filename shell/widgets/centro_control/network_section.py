"""Network section for the control center (Ethernet + Wi-Fi)."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .hotspot_section import ControlCenterHotspotSection
from ...models import NetworkSnapshot, WifiAccessPointSnapshot
from ...servicios.red.network import (
    NetworkService,
    build_ethernet_tooltip,
    ethernet_icon_name,
    format_link_speed,
)
from ...ui import ShellModule


class ControlCenterNetworkSection(ShellModule):
    """Ethernet and Wi-Fi controls backed by NetworkService."""

    def __init__(
        self,
        network_service: NetworkService,
        *,
        on_toggle_ethernet: Callable[[], None],
        on_toggle_wireless: Callable[[bool], None],
        on_request_wifi_scan: Callable[[], None],
        on_connect_wifi: Callable[[str, str], None],
        on_disconnect_wifi: Callable[[], None],
        on_toggle_hotspot: Callable[[bool, str, str, str], None],
        on_apply_hotspot: Callable[[str, str, str], None],
    ) -> None:
        super().__init__("control-center-network-section", spacing=0)
        self._service = network_service
        self._on_toggle_ethernet = on_toggle_ethernet
        self._on_toggle_wireless = on_toggle_wireless
        self._on_request_wifi_scan = on_request_wifi_scan
        self._on_connect_wifi = on_connect_wifi
        self._on_disconnect_wifi = on_disconnect_wifi
        self._pending_ap_path: str | None = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.get_style_context().add_class("control-center-section")
        self.pack_start(outer, True, True, 0)

        title = Gtk.Label(xalign=0)
        title.get_style_context().add_class("control-center-section-title")
        title.set_markup("<b>Red</b>")
        outer.pack_start(title, False, False, 0)

        outer.pack_start(self._build_ethernet_block(), False, False, 0)
        outer.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        outer.pack_start(self._build_wifi_block(), False, False, 0)
        outer.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        self._hotspot_section = ControlCenterHotspotSection(
            on_toggle_hotspot=on_toggle_hotspot,
            on_apply_hotspot=on_apply_hotspot,
        )
        outer.pack_start(self._hotspot_section, False, False, 0)

    def refresh(self, snapshot: NetworkSnapshot) -> None:
        self._sync_ethernet(snapshot)
        self._sync_wifi(snapshot)
        self._hotspot_section.refresh(snapshot)

    def _build_ethernet_block(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("control-center-subsection")

        heading_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._ethernet_icon = Gtk.Image.new_from_icon_name(
            "network-wired-disconnected-symbolic",
            Gtk.IconSize.MENU,
        )
        self._ethernet_icon.get_style_context().add_class("control-center-ethernet-icon")
        heading_row.pack_start(self._ethernet_icon, False, False, 0)
        heading = Gtk.Label(label="Ethernet", xalign=0)
        heading.get_style_context().add_class("control-center-subsection-title")
        heading_row.pack_start(heading, True, True, 0)
        box.pack_start(heading_row, False, False, 0)

        self._ethernet_status = Gtk.Label(xalign=0)
        self._ethernet_status.get_style_context().add_class("control-center-detail")
        box.pack_start(self._ethernet_status, False, False, 0)

        self._ethernet_details = Gtk.Label(xalign=0)
        self._ethernet_details.get_style_context().add_class("control-center-detail-muted")
        self._ethernet_details.set_line_wrap(True)
        box.pack_start(self._ethernet_details, False, False, 0)

        self._ethernet_button = Gtk.Button(label="Alternar conexión", relief=Gtk.ReliefStyle.NONE)
        self._ethernet_button.get_style_context().add_class("control-center-action")
        self._ethernet_button.connect("clicked", lambda *_args: self._on_toggle_ethernet())
        box.pack_start(self._ethernet_button, False, False, 0)
        return box

    def _build_wifi_block(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("control-center-subsection")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label="Wi-Fi", xalign=0)
        heading.get_style_context().add_class("control-center-subsection-title")
        heading.set_hexpand(True)
        header.pack_start(heading, True, True, 0)

        self._wifi_switch = Gtk.Switch()
        self._wifi_switch.set_halign(Gtk.Align.END)
        self._wifi_switch.connect("notify::active", self._on_wifi_switch_changed)
        header.pack_start(self._wifi_switch, False, False, 0)
        box.pack_start(header, False, False, 0)

        self._wifi_status = Gtk.Label(xalign=0)
        self._wifi_status.get_style_context().add_class("control-center-detail")
        box.pack_start(self._wifi_status, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        self._wifi_disconnect_button = Gtk.Button(label="Desconectar", relief=Gtk.ReliefStyle.NONE)
        self._wifi_disconnect_button.get_style_context().add_class("control-center-action")
        self._wifi_disconnect_button.connect("clicked", lambda *_args: self._on_disconnect_wifi())
        actions.pack_start(self._wifi_disconnect_button, False, False, 0)

        scan_button = Gtk.Button(label="Buscar redes", relief=Gtk.ReliefStyle.NONE)
        scan_button.get_style_context().add_class("control-center-action")
        scan_button.connect("clicked", lambda *_args: self._on_request_wifi_scan())
        actions.pack_start(scan_button, False, False, 0)
        box.pack_start(actions, False, False, 0)

        self._wifi_list = Gtk.ListBox()
        self._wifi_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._wifi_list.get_style_context().add_class("control-center-wifi-list")
        box.pack_start(self._wifi_list, False, False, 0)

        password_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._wifi_password_entry = Gtk.Entry()
        self._wifi_password_entry.set_visibility(False)
        self._wifi_password_entry.set_placeholder_text("Contraseña Wi-Fi")
        self._wifi_password_entry.set_hexpand(True)
        password_row.pack_start(self._wifi_password_entry, True, True, 0)
        self._wifi_password_button = Gtk.Button(label="Conectar", relief=Gtk.ReliefStyle.NONE)
        self._wifi_password_button.get_style_context().add_class("control-center-action")
        self._wifi_password_button.connect("clicked", self._on_wifi_password_connect)
        password_row.pack_start(self._wifi_password_button, False, False, 0)
        self._wifi_password_row = password_row
        self._wifi_password_row.set_no_show_all(True)
        box.pack_start(self._wifi_password_row, False, False, 0)
        return box

    def _sync_ethernet(self, snapshot: NetworkSnapshot) -> None:
        ethernet = snapshot.ethernet
        if ethernet is None:
            self._ethernet_icon.set_from_icon_name(
                "network-wired-disconnected-symbolic",
                Gtk.IconSize.MENU,
            )
            self._ethernet_status.set_text("No hay interfaz Ethernet disponible")
            self._ethernet_details.set_text("")
            self._ethernet_button.set_sensitive(False)
            return

        icon_name = ethernet_icon_name(snapshot)
        if icon_name is not None:
            self._ethernet_icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)
        self._ethernet_button.set_sensitive(True)
        if ethernet.state == "connected":
            internet = snapshot.connectivity.summary_label
            self._ethernet_status.set_text(f"Conectado · {internet}")
        else:
            labels = {
                "disconnected": "Desconectado",
                "connecting": "Conectando…",
                "disconnecting": "Desconectando…",
                "unavailable": "No disponible",
                "failed": "Error",
            }
            self._ethernet_status.set_text(labels.get(ethernet.state, ethernet.state))

        details: list[str] = []
        if ethernet.interface:
            details.append(f"Interfaz: {ethernet.interface}")
        if ethernet.ip_address:
            details.append(f"IP: {ethernet.ip_address}")
        speed = format_link_speed(ethernet.speed_mbps)
        if speed:
            details.append(f"Velocidad: {speed}")
        if ethernet.connection_name:
            details.append(f"Perfil: {ethernet.connection_name}")
        self._ethernet_details.set_text("\n".join(details))
        self._ethernet_button.set_tooltip_text(build_ethernet_tooltip(snapshot))

    def _sync_wifi(self, snapshot: NetworkSnapshot) -> None:
        self._wifi_switch.handler_block_by_func(self._on_wifi_switch_changed)
        self._wifi_switch.set_sensitive(snapshot.wireless_hardware_enabled)
        self._wifi_switch.set_active(snapshot.wireless_enabled)
        self._wifi_switch.handler_unblock_by_func(self._on_wifi_switch_changed)

        wifi = snapshot.wifi
        if not snapshot.wireless_hardware_enabled:
            self._wifi_status.set_text("Wi-Fi no disponible en este equipo")
        elif not snapshot.wireless_enabled:
            self._wifi_status.set_text("Wi-Fi desactivado")
        elif wifi is None:
            self._wifi_status.set_text("Sin adaptador Wi-Fi gestionado")
        elif wifi.state == "connected":
            ssid = wifi.connection_name or wifi.interface
            self._wifi_status.set_text(f"Conectado a {ssid}")
        else:
            labels = {
                "disconnected": "Desconectado",
                "connecting": "Conectando…",
                "unavailable": "No disponible",
            }
            self._wifi_status.set_text(labels.get(wifi.state, wifi.state))

        self._wifi_disconnect_button.set_sensitive(
            wifi is not None and wifi.connected and not snapshot.hotspot.active,
        )

        for child in self._wifi_list.get_children():
            self._wifi_list.remove(child)

        if not snapshot.wireless_enabled:
            self._wifi_password_row.hide()
            return

        if not snapshot.wifi_access_points:
            empty = Gtk.Label(label="No se encontraron redes", xalign=0)
            empty.get_style_context().add_class("control-center-detail-muted")
            self._wifi_list.add(empty)
            self._wifi_list.show_all()
            return

        for access_point in snapshot.wifi_access_points:
            self._wifi_list.add(self._wifi_row(access_point))
        self._wifi_list.show_all()

    def _wifi_row(self, access_point: WifiAccessPointSnapshot) -> Gtk.Widget:
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("control-center-wifi-row")
        if access_point.active:
            row.get_style_context().add_class("control-center-wifi-row-active")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        row.add(box)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_box.set_hexpand(True)
        title = Gtk.Label(xalign=0)
        title.get_style_context().add_class("control-center-wifi-ssid")
        title.set_markup(f"<b>{access_point.ssid}</b>")
        text_box.pack_start(title, False, False, 0)

        meta_parts = [f"{access_point.strength}%"]
        if access_point.frequency_mhz:
            meta_parts.append(f"{access_point.frequency_mhz} MHz")
        if access_point.secured:
            meta_parts.append("Protegida")
        meta = Gtk.Label(label=" · ".join(meta_parts), xalign=0)
        meta.get_style_context().add_class("control-center-detail-muted")
        text_box.pack_start(meta, False, False, 0)
        box.pack_start(text_box, True, True, 0)

        button = Gtk.Button(label="Conectar", relief=Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("control-center-action")
        button.connect(
            "clicked",
            lambda _btn, ap=access_point: self._on_wifi_row_connect(ap),
        )
        box.pack_start(button, False, False, 0)
        return row

    def _on_wifi_switch_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._on_toggle_wireless(switch.get_active())

    def _on_wifi_row_connect(self, access_point: WifiAccessPointSnapshot) -> None:
        if access_point.secured:
            self._pending_ap_path = access_point.path
            self._wifi_password_entry.set_text("")
            self._wifi_password_row.show_all()
            self._wifi_password_entry.grab_focus()
            return
        self._pending_ap_path = None
        self._wifi_password_row.hide()
        self._on_connect_wifi(access_point.path, "")

    def _on_wifi_password_connect(self, *_args) -> None:
        if self._pending_ap_path is None:
            return
        self._on_connect_wifi(
            self._pending_ap_path,
            self._wifi_password_entry.get_text(),
        )
