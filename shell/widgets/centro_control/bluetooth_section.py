"""Bluetooth section for the control center."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...models import BluetoothDeviceSnapshot, BluetoothSnapshot
from ...servicios.bluetooth.bluetooth import BluetoothService, bluetooth_icon_name
from ...ui import ShellModule
from ...ui.image_files import choose_file_path


def _format_bytes(size: int | None) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class ControlCenterBluetoothSection(ShellModule):
    """Adapter power, discovery, file transfer, and device actions."""

    def __init__(
        self,
        bluetooth_service: BluetoothService,
        *,
        on_toggle_powered: Callable[[bool], None],
        on_start_discovery: Callable[[], None],
        on_stop_discovery: Callable[[], None],
        on_set_receiving: Callable[[bool], None],
        on_connect: Callable[[str], None],
        on_disconnect: Callable[[str], None],
        on_pair: Callable[[str], None],
        on_remove: Callable[[str], None],
        on_send_file: Callable[[str, str], None],
        on_cancel_transfer: Callable[[], None],
        on_accept_incoming: Callable[[], None],
        on_reject_incoming: Callable[[], None],
        on_accept_pairing: Callable[..., None],
        on_reject_pairing: Callable[[], None],
    ) -> None:
        super().__init__("control-center-bluetooth-section", spacing=0)
        self._service = bluetooth_service
        self._on_toggle_powered = on_toggle_powered
        self._on_start_discovery = on_start_discovery
        self._on_stop_discovery = on_stop_discovery
        self._on_set_receiving = on_set_receiving
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_pair = on_pair
        self._on_remove = on_remove
        self._on_send_file = on_send_file
        self._on_cancel_transfer = on_cancel_transfer
        self._on_accept_incoming = on_accept_incoming
        self._on_reject_incoming = on_reject_incoming
        self._on_accept_pairing = on_accept_pairing
        self._on_reject_pairing = on_reject_pairing

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.get_style_context().add_class("control-center-section")
        self.pack_start(outer, True, True, 0)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._title_icon = Gtk.Image.new_from_icon_name(
            "bluetooth-symbolic",
            Gtk.IconSize.MENU,
        )
        title_row.pack_start(self._title_icon, False, False, 0)
        title = Gtk.Label(xalign=0)
        title.get_style_context().add_class("control-center-section-title")
        title.set_markup("<b>Bluetooth</b>")
        title.set_hexpand(True)
        title_row.pack_start(title, True, True, 0)
        self._power_switch = Gtk.Switch()
        self._power_switch.set_halign(Gtk.Align.END)
        self._power_switch.connect("notify::active", self._on_power_switch_changed)
        title_row.pack_start(self._power_switch, False, False, 0)
        outer.pack_start(title_row, False, False, 0)

        self._status = Gtk.Label(xalign=0)
        self._status.get_style_context().add_class("control-center-detail")
        outer.pack_start(self._status, False, False, 0)

        self._pairing_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._pairing_box.get_style_context().add_class("control-center-subsection")
        self._pairing_hint = Gtk.Label(xalign=0)
        self._pairing_hint.get_style_context().add_class("control-center-detail")
        self._pairing_hint.set_line_wrap(True)
        self._pairing_box.pack_start(self._pairing_hint, False, False, 0)
        self._pairing_entry = Gtk.Entry()
        self._pairing_entry.set_placeholder_text("PIN / código")
        self._pairing_box.pack_start(self._pairing_entry, False, False, 0)
        pairing_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pairing_actions.set_halign(Gtk.Align.END)
        self._pairing_accept = Gtk.Button(label="Aceptar", relief=Gtk.ReliefStyle.NONE)
        self._pairing_accept.get_style_context().add_class("control-center-action")
        self._pairing_accept.connect("clicked", self._on_accept_clicked)
        pairing_actions.pack_start(self._pairing_accept, False, False, 0)
        self._pairing_reject = Gtk.Button(label="Rechazar", relief=Gtk.ReliefStyle.NONE)
        self._pairing_reject.get_style_context().add_class("control-center-action")
        self._pairing_reject.connect("clicked", lambda *_: self._on_reject_pairing())
        pairing_actions.pack_start(self._pairing_reject, False, False, 0)
        self._pairing_box.pack_start(pairing_actions, False, False, 0)
        outer.pack_start(self._pairing_box, False, False, 0)

        self._incoming_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._incoming_box.get_style_context().add_class("control-center-subsection")
        self._incoming_hint = Gtk.Label(xalign=0)
        self._incoming_hint.get_style_context().add_class("control-center-detail")
        self._incoming_hint.set_line_wrap(True)
        self._incoming_box.pack_start(self._incoming_hint, False, False, 0)
        incoming_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        incoming_actions.set_halign(Gtk.Align.END)
        accept_in = Gtk.Button(label="Recibir archivo", relief=Gtk.ReliefStyle.NONE)
        accept_in.get_style_context().add_class("control-center-action")
        accept_in.connect("clicked", lambda *_: self._on_accept_incoming())
        incoming_actions.pack_start(accept_in, False, False, 0)
        reject_in = Gtk.Button(label="Rechazar", relief=Gtk.ReliefStyle.NONE)
        reject_in.get_style_context().add_class("control-center-action")
        reject_in.connect("clicked", lambda *_: self._on_reject_incoming())
        incoming_actions.pack_start(reject_in, False, False, 0)
        self._incoming_box.pack_start(incoming_actions, False, False, 0)
        outer.pack_start(self._incoming_box, False, False, 0)

        self._transfer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._transfer_box.get_style_context().add_class("control-center-subsection")
        self._transfer_label = Gtk.Label(xalign=0)
        self._transfer_label.get_style_context().add_class("control-center-detail")
        self._transfer_label.set_line_wrap(True)
        self._transfer_box.pack_start(self._transfer_label, False, False, 0)
        self._transfer_bar = Gtk.ProgressBar()
        self._transfer_bar.set_show_text(False)
        self._transfer_box.pack_start(self._transfer_bar, False, False, 0)
        cancel = Gtk.Button(label="Cancelar transferencia", relief=Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("control-center-action")
        cancel.set_halign(Gtk.Align.END)
        cancel.connect("clicked", lambda *_: self._on_cancel_transfer())
        self._transfer_cancel = cancel
        self._transfer_box.pack_start(cancel, False, False, 0)
        outer.pack_start(self._transfer_box, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        self._receive_button = Gtk.Button(label="Recibir archivos", relief=Gtk.ReliefStyle.NONE)
        self._receive_button.get_style_context().add_class("control-center-action")
        self._receive_button.connect("clicked", self._on_receive_clicked)
        actions.pack_start(self._receive_button, False, False, 0)
        self._scan_button = Gtk.Button(label="Buscar dispositivos", relief=Gtk.ReliefStyle.NONE)
        self._scan_button.get_style_context().add_class("control-center-action")
        self._scan_button.connect("clicked", self._on_scan_clicked)
        actions.pack_start(self._scan_button, False, False, 0)
        outer.pack_start(actions, False, False, 0)

        self._device_list = Gtk.ListBox()
        self._device_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._device_list.get_style_context().add_class("control-center-bluetooth-list")
        outer.pack_start(self._device_list, False, False, 0)

    def refresh(self, snapshot: BluetoothSnapshot) -> None:
        self._title_icon.set_from_icon_name(bluetooth_icon_name(snapshot), Gtk.IconSize.MENU)

        self._power_switch.handler_block_by_func(self._on_power_switch_changed)
        self._power_switch.set_sensitive(snapshot.available)
        self._power_switch.set_active(snapshot.available and snapshot.powered)
        self._power_switch.handler_unblock_by_func(self._on_power_switch_changed)

        if not snapshot.available:
            self._status.set_text(
                snapshot.error_message
                or "BlueZ no está instalado o el servicio no está disponible."
            )
            self._scan_button.set_sensitive(False)
            self._receive_button.set_sensitive(False)
            self._pairing_box.hide()
            self._incoming_box.hide()
            self._transfer_box.hide()
            self._clear_devices()
            return

        if not snapshot.powered:
            self._status.set_text("Apagado")
            self._scan_button.set_sensitive(False)
            self._receive_button.set_sensitive(False)
            self._scan_button.set_label("Buscar dispositivos")
            self._receive_button.set_label("Recibir archivos")
            self._pairing_box.hide()
            self._incoming_box.hide()
            self._transfer_box.hide()
            self._clear_devices()
            return

        if snapshot.error_message:
            self._status.set_text(snapshot.error_message)
        elif snapshot.receiving:
            self._status.set_text("Visible · listo para recibir archivos")
        elif snapshot.discovering:
            self._status.set_text("Buscando dispositivos…")
        elif snapshot.connected_devices:
            names = ", ".join(device.name for device in snapshot.connected_devices[:2])
            self._status.set_text(f"Encendido · {names}")
        else:
            self._status.set_text("Encendido")

        self._scan_button.set_sensitive(True)
        self._scan_button.set_label(
            "Detener búsqueda" if snapshot.discovering else "Buscar dispositivos"
        )
        self._receive_button.set_sensitive(True)
        self._receive_button.set_label(
            "Dejar de recibir" if snapshot.receiving else "Recibir archivos"
        )
        self._sync_pairing(snapshot)
        self._sync_incoming(snapshot)
        self._sync_transfer(snapshot)
        self._sync_devices(snapshot)

    def _sync_pairing(self, snapshot: BluetoothSnapshot) -> None:
        pairing = snapshot.pairing
        if pairing is None:
            self._pairing_box.hide()
            return
        name = pairing.device_name or pairing.device_address or "Dispositivo"
        hint = pairing.hint or "Confirmar emparejamiento"
        self._pairing_hint.set_text(f"{name}\n{hint}")
        needs_input = pairing.kind in {"pin", "passkey"}
        interactive = pairing.kind in {"confirmation", "authorization", "pin", "passkey"}
        self._pairing_entry.set_visible(needs_input)
        self._pairing_entry.set_sensitive(needs_input)
        if not needs_input:
            self._pairing_entry.set_text("")
        self._pairing_accept.set_sensitive(interactive)
        self._pairing_reject.set_sensitive(interactive)
        self._pairing_accept.set_visible(interactive)
        self._pairing_reject.set_visible(interactive)
        self._pairing_box.show_all()
        if not needs_input:
            self._pairing_entry.hide()

    def _sync_incoming(self, snapshot: BluetoothSnapshot) -> None:
        incoming = snapshot.incoming_file
        if incoming is None:
            self._incoming_box.hide()
            return
        size = _format_bytes(incoming.size)
        detail = f"Archivo entrante: {incoming.name}"
        if size:
            detail = f"{detail} ({size})"
        if incoming.suggested_path:
            detail = f"{detail}\nSe guardará en Descargas"
        self._incoming_hint.set_text(detail)
        self._incoming_box.show_all()

    def _sync_transfer(self, snapshot: BluetoothSnapshot) -> None:
        transfer = snapshot.transfer
        if transfer is None or not transfer.status:
            self._transfer_box.hide()
            return
        direction = "Enviando" if transfer.direction == "send" else "Recibiendo"
        if transfer.status == "complete":
            direction = "Completado"
        elif transfer.status == "error":
            direction = "Error"
        size = _format_bytes(transfer.size)
        done = _format_bytes(transfer.transferred)
        parts = [f"{direction}: {transfer.name or 'archivo'}"]
        if size and done:
            parts.append(f"{done} / {size}")
        elif size:
            parts.append(size)
        if transfer.error_message:
            parts.append(transfer.error_message)
        self._transfer_label.set_text("\n".join(parts))
        self._transfer_bar.set_fraction(transfer.progress)
        self._transfer_cancel.set_sensitive(transfer.active)
        self._transfer_cancel.set_visible(transfer.active)
        self._transfer_box.show_all()
        if not transfer.active:
            self._transfer_cancel.hide()

    def _on_accept_clicked(self, *_args) -> None:
        pairing = self._service.snapshot.pairing
        if pairing is None:
            return
        if pairing.kind == "pin":
            self._on_accept_pairing(pin=self._pairing_entry.get_text().strip())
            return
        if pairing.kind == "passkey":
            raw = self._pairing_entry.get_text().strip()
            try:
                value = int(raw) if raw else None
            except ValueError:
                value = None
            self._on_accept_pairing(passkey=value)
            return
        self._on_accept_pairing()

    def _sync_devices(self, snapshot: BluetoothSnapshot) -> None:
        self._clear_devices()
        devices = snapshot.devices
        if not devices:
            empty = Gtk.Label(label="No hay dispositivos conectados", xalign=0)
            empty.get_style_context().add_class("control-center-detail-muted")
            self._device_list.add(empty)
            self._device_list.show_all()
            return
        for device in devices:
            self._device_list.add(self._device_row(device, snapshot.obex_available))
        self._device_list.show_all()

    def _clear_devices(self) -> None:
        for child in self._device_list.get_children():
            self._device_list.remove(child)

    def _device_row(self, device: BluetoothDeviceSnapshot, obex_available: bool) -> Gtk.Widget:
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("control-center-bluetooth-row")
        if device.connected:
            row.get_style_context().add_class("control-center-bluetooth-row-active")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_name = device.icon or "bluetooth-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        header.pack_start(icon, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        name = Gtk.Label(label=device.name, xalign=0)
        name.get_style_context().add_class("control-center-bluetooth-name")
        text.pack_start(name, False, False, 0)

        if device.connected:
            status = "● Conectado"
        elif device.paired:
            status = "○ Desconectado"
        else:
            status = "Disponible"
        if device.device_type:
            status = f"{device.device_type} · {status}"
        if device.battery_percent is not None:
            status = f"{status} · Batería {device.battery_percent}%"
        detail = Gtk.Label(label=status, xalign=0)
        detail.get_style_context().add_class("control-center-detail-muted")
        text.pack_start(detail, False, False, 0)
        header.pack_start(text, True, True, 0)
        box.pack_start(header, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        if device.connected:
            button = Gtk.Button(label="Desconectar", relief=Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("control-center-action")
            button.connect(
                "clicked",
                lambda *_args, address=device.address: self._on_disconnect(address),
            )
            actions.pack_start(button, False, False, 0)
        elif device.paired:
            button = Gtk.Button(label="Conectar", relief=Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("control-center-action")
            button.connect(
                "clicked",
                lambda *_args, address=device.address: self._on_connect(address),
            )
            actions.pack_start(button, False, False, 0)
        else:
            button = Gtk.Button(label="Emparejar", relief=Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("control-center-action")
            button.connect(
                "clicked",
                lambda *_args, address=device.address: self._on_pair(address),
            )
            actions.pack_start(button, False, False, 0)

        if device.paired and (device.can_send_files or obex_available):
            send = Gtk.Button(label="Enviar", relief=Gtk.ReliefStyle.NONE)
            send.get_style_context().add_class("control-center-action")
            send.set_sensitive(obex_available)
            send.set_tooltip_text(
                "Enviar archivo por Bluetooth"
                if obex_available
                else "OBEX no disponible (bluez-obex)"
            )
            send.connect(
                "clicked",
                lambda *_args, address=device.address: self._pick_and_send(address),
            )
            actions.pack_start(send, False, False, 0)

        if device.paired or device.connected:
            remove = Gtk.Button(label="Olvidar", relief=Gtk.ReliefStyle.NONE)
            remove.get_style_context().add_class("control-center-action")
            remove.connect(
                "clicked",
                lambda *_args, address=device.address: self._on_remove(address),
            )
            actions.pack_start(remove, False, False, 0)

        box.pack_start(actions, False, False, 0)
        row.add(box)
        return row

    def _pick_and_send(self, address: str) -> None:
        parent = self.get_toplevel()
        window = parent if isinstance(parent, Gtk.Window) else None
        path = choose_file_path(window, title="Enviar por Bluetooth")
        if path is None:
            return
        self._on_send_file(address, str(path))

    def _on_power_switch_changed(self, switch: Gtk.Switch, *_args) -> None:
        self._on_toggle_powered(bool(switch.get_active()))

    def _on_scan_clicked(self, *_args) -> None:
        if self._service.snapshot.discovering:
            self._on_stop_discovery()
        else:
            self._on_start_discovery()

    def _on_receive_clicked(self, *_args) -> None:
        self._on_set_receiving(not self._service.snapshot.receiving)
