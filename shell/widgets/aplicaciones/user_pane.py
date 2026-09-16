"""User + machine profile panel for the unified control center."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, Gtk

from ...eventbus import EventBus
from ...servicios.sistema.system import SystemStatsService, format_capacity_bytes
from ...settings.manager import SETTINGS_CHANGED, SettingsManager
from ...settings.profile_model import parse_profile_fields
from ...ui.image_files import (
    choose_image_path,
    circular_pixbuf,
    load_cover_pixbuf,
    rounded_pixbuf,
)
from ...ui.profile_images import (
    AVATAR_SETTING_KEY,
    MACHINE_SETTING_KEY,
    install_profile_image,
    resolve_profile_image,
)

_AVATAR_SIZE = 96
_MACHINE_IMAGE_SIZE = 72
_MACHINE_IMAGE_RADIUS = 14.0
_AVATAR_FALLBACK_ICON = "avatar-default-symbolic"
_MACHINE_FALLBACK_ICON = "computer-symbolic"
_DEFAULT_AVATAR_CANDIDATES = (Path("~/.face"), Path("~/.face.icon"))
_PROFILE_KEYS = (
    AVATAR_SETTING_KEY,
    MACHINE_SETTING_KEY,
    "general.profile_fields_json",
)


class UserPane(Gtk.Box):
    """Personal + machine summary; keeps a fixed width and scrolls internally."""

    def __init__(
        self,
        manager: SettingsManager,
        event_bus: EventBus,
        system_stats: SystemStatsService | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._manager = manager
        self._event_bus = event_bus
        self._system_stats = system_stats or SystemStatsService()
        self.get_style_context().add_class("control-center-user-pane")
        self.set_hexpand(False)
        self.set_vexpand(True)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(False)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("control-center-user-scroll")
        self.pack_start(scroll, True, True, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.get_style_context().add_class("control-center-user-body")
        scroll.add(body)

        # —— Usuario ——
        self._avatar_button = self._make_image_button(
            size=_AVATAR_SIZE,
            css_class="control-center-user-avatar-frame",
            tooltip="Cambiar avatar",
            on_click=self._pick_avatar,
        )
        body.pack_start(self._avatar_button, False, False, 0)

        self._name = Gtk.Label(xalign=0.5)
        self._name.get_style_context().add_class("control-center-user-name")
        self._name.set_line_wrap(True)
        self._name.set_max_width_chars(18)
        self._name.set_justify(Gtk.Justification.CENTER)
        body.pack_start(self._name, False, False, 0)

        self._meta = Gtk.Label(xalign=0.5)
        self._meta.get_style_context().add_class("control-center-user-meta")
        self._meta.set_single_line_mode(True)
        self._meta.set_justify(Gtk.Justification.CENTER)
        body.pack_start(self._meta, False, False, 0)

        self._custom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._custom_box.get_style_context().add_class("control-center-user-custom")
        body.pack_start(self._custom_box, False, False, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.get_style_context().add_class("control-center-user-separator")
        body.pack_start(separator, False, False, 4)

        # —— PC ——
        self._machine_button = self._make_image_button(
            size=_MACHINE_IMAGE_SIZE,
            css_class="control-center-machine-image-frame",
            tooltip="Cambiar foto de la PC",
            on_click=self._pick_machine_image,
        )
        body.pack_start(self._machine_button, False, False, 0)

        self._hostname = Gtk.Label(xalign=0.5)
        self._hostname.get_style_context().add_class("control-center-machine-name")
        self._hostname.set_line_wrap(True)
        self._hostname.set_max_width_chars(18)
        self._hostname.set_justify(Gtk.Justification.CENTER)
        body.pack_start(self._hostname, False, False, 0)

        self._hw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._hw_box.get_style_context().add_class("control-center-machine-hw")
        body.pack_start(self._hw_box, False, False, 0)

        event_bus.subscribe(SETTINGS_CHANGED, self._on_settings_changed)
        self.connect("destroy", self._on_destroy)
        self.connect("map", self._on_map)
        self.refresh()

    def refresh(self) -> None:
        user = pwd.getpwuid(os.getuid())
        gecos = (user.pw_gecos or "").split(",", 1)[0].strip()
        display_name = gecos or user.pw_name
        self._name.set_text(display_name)
        self._meta.set_text(f"@{user.pw_name}")
        self._set_shaped_image(
            self._avatar_button,
            self._resolve_avatar_path(),
            size=_AVATAR_SIZE,
            shape="circle",
            fallback=_AVATAR_FALLBACK_ICON,
        )
        self._rebuild_custom_fields()

        identity = self._system_stats.identity_summary()
        hostname = identity.hostname or "PC"
        self._hostname.set_text(hostname)
        self._set_shaped_image(
            self._machine_button,
            self._resolve_machine_image_path(),
            size=_MACHINE_IMAGE_SIZE,
            shape="rounded",
            fallback=_MACHINE_FALLBACK_ICON,
        )
        self._rebuild_hardware(identity)

    def _make_image_button(
        self,
        *,
        size: int,
        css_class: str,
        tooltip: str,
        on_click,
    ) -> Gtk.EventBox:
        button = Gtk.EventBox()
        button.set_visible_window(True)
        button.get_style_context().add_class(css_class)
        button.set_size_request(size, size)
        button.set_halign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip)
        button.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        button.connect("button-press-event", lambda _w, event: self._on_image_click(event, on_click))

        image = Gtk.Image()
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        image.set_size_request(size, size)
        button.add(image)
        button._image = image  # type: ignore[attr-defined]
        return button

    def _on_image_click(self, event, callback) -> bool:
        if getattr(event, "button", 1) != 1:
            return False
        callback()
        return True

    def _pick_avatar(self) -> None:
        path = choose_image_path(
            self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None,
            title="Seleccionar avatar",
        )
        if path is None:
            return
        try:
            installed = install_profile_image(path, AVATAR_SETTING_KEY)
        except OSError as error:
            print(f"shell: avatar install failed: {error}", flush=True)
            return
        self._manager.set(AVATAR_SETTING_KEY, str(installed))

    def _pick_machine_image(self) -> None:
        path = choose_image_path(
            self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None,
            title="Seleccionar foto de la PC",
        )
        if path is None:
            return
        try:
            installed = install_profile_image(path, MACHINE_SETTING_KEY)
        except OSError as error:
            print(f"shell: machine image install failed: {error}", flush=True)
            return
        self._manager.set(MACHINE_SETTING_KEY, str(installed))

    def _resolve_avatar_path(self) -> Path | None:
        installed = resolve_profile_image(AVATAR_SETTING_KEY)
        if installed is not None:
            return installed
        # Legacy: path stored before assets/usuario copies existed.
        configured = str(self._manager.get(AVATAR_SETTING_KEY) or "").strip()
        if configured:
            configured_path = Path(configured).expanduser()
            if configured_path.is_file():
                return configured_path
        for candidate in _DEFAULT_AVATAR_CANDIDATES:
            resolved = candidate.expanduser()
            if resolved.is_file():
                return resolved
        return None

    def _resolve_machine_image_path(self) -> Path | None:
        installed = resolve_profile_image(MACHINE_SETTING_KEY)
        if installed is not None:
            return installed
        configured = str(self._manager.get(MACHINE_SETTING_KEY) or "").strip()
        if not configured:
            return None
        path = Path(configured).expanduser()
        return path if path.is_file() else None

    def _set_shaped_image(
        self,
        button: Gtk.EventBox,
        path: Path | None,
        *,
        size: int,
        shape: str,
        fallback: str,
    ) -> None:
        image: Gtk.Image = button._image  # type: ignore[attr-defined]
        if path is not None:
            cover = load_cover_pixbuf(path, size)
            if cover is not None:
                shaped = (
                    circular_pixbuf(cover, size)
                    if shape == "circle"
                    else rounded_pixbuf(cover, size, _MACHINE_IMAGE_RADIUS)
                )
                if shaped is not None:
                    image.set_from_pixbuf(shaped)
                    return
        image.set_from_icon_name(fallback, Gtk.IconSize.DIALOG)
        image.set_pixel_size(size)

    def _rebuild_custom_fields(self) -> None:
        for child in list(self._custom_box.get_children()):
            self._custom_box.remove(child)
            child.destroy()
        for field in parse_profile_fields(str(self._manager.get("general.profile_fields_json") or "")):
            if not field.title and not field.value:
                continue
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            block.get_style_context().add_class("control-center-user-field")
            title = Gtk.Label(label=field.title, xalign=0.5)
            title.get_style_context().add_class("control-center-user-field-title")
            title.set_line_wrap(True)
            title.set_max_width_chars(18)
            title.set_justify(Gtk.Justification.CENTER)
            value = Gtk.Label(label=field.value, xalign=0.5)
            value.get_style_context().add_class("control-center-user-field-value")
            value.set_line_wrap(True)
            value.set_max_width_chars(18)
            value.set_justify(Gtk.Justification.CENTER)
            block.pack_start(title, False, False, 0)
            block.pack_start(value, False, False, 0)
            self._custom_box.pack_start(block, False, False, 0)
        self._custom_box.show_all()

    def _rebuild_hardware(self, identity) -> None:
        for child in list(self._hw_box.get_children()):
            self._hw_box.remove(child)
            child.destroy()
        rows: list[tuple[str, str]] = []
        if identity.cpu_model:
            rows.append(("CPU", identity.cpu_model))
        if identity.gpu_name:
            rows.append(("GPU", identity.gpu_name))
        ram = format_capacity_bytes(identity.ram_total_bytes)
        if ram:
            rows.append(("RAM", ram))
        for label, value in rows:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            row.get_style_context().add_class("control-center-hw-row")
            key = Gtk.Label(label=label, xalign=0.5)
            key.get_style_context().add_class("control-center-hw-key")
            val = Gtk.Label(label=value, xalign=0.5)
            val.get_style_context().add_class("control-center-hw-value")
            val.set_line_wrap(True)
            val.set_max_width_chars(18)
            val.set_justify(Gtk.Justification.CENTER)
            row.pack_start(key, False, False, 0)
            row.pack_start(val, False, False, 0)
            self._hw_box.pack_start(row, False, False, 0)
        self._hw_box.show_all()

    def _on_map(self, *_args) -> None:
        # Refresh hostname / sensors when the pane becomes visible again.
        self.refresh()

    def _on_settings_changed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("key") in _PROFILE_KEYS:
            self.refresh()

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(SETTINGS_CHANGED, self._on_settings_changed)
