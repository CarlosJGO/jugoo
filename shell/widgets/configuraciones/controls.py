"""Reusable GTK rows for the Settings Center."""

from __future__ import annotations

from typing import Any, Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...settings.schema import APPLY_LIVE, SettingDef


class SettingRow(Gtk.Box):
    """One preference row: label + control + apply hint."""

    def __init__(
        self,
        definition: SettingDef,
        value: Any,
        *,
        choices: tuple[tuple[str, str], ...] = (),
        on_change: Callable[[str, Any], None],
        apply_label: str,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("settings-row")
        self._definition = definition
        self._on_change = on_change
        self._suppress = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)

        title = Gtk.Label(label=definition.label, xalign=0)
        title.get_style_context().add_class("settings-row-title")
        texts.pack_start(title, False, False, 0)

        if definition.description:
            desc = Gtk.Label(label=definition.description, xalign=0)
            desc.get_style_context().add_class("settings-row-desc")
            desc.set_line_wrap(True)
            desc.set_max_width_chars(48)
            texts.pack_start(desc, False, False, 0)
        header.pack_start(texts, True, True, 0)

        control = self._build_control(value, choices)
        header.pack_end(control, False, False, 0)
        self.pack_start(header, False, False, 0)

        if definition.apply != APPLY_LIVE:
            hint = Gtk.Label(label=apply_label, xalign=0)
            hint.get_style_context().add_class("settings-row-hint")
            self.pack_start(hint, False, False, 0)

    def _build_control(self, value: Any, choices: tuple[tuple[str, str], ...]) -> Gtk.Widget:
        definition = self._definition
        if definition.value_type == "bool":
            switch = Gtk.Switch()
            switch.set_active(bool(value))
            switch.set_valign(Gtk.Align.CENTER)
            switch.connect("notify::active", self._on_switch)
            return switch

        if definition.value_type == "choice" or (
            definition.value_type in {"string", "path"} and choices
        ):
            combo = Gtk.ComboBoxText()
            combo.get_style_context().add_class("settings-combo")
            selected = str(value)
            # GTK rejects empty ComboBox ids; Apariencia maps "" → __system__.
            if selected == "" and any(item == "__system__" for item, _label in choices):
                selected = "__system__"
            active = 0
            for index, (item_value, label) in enumerate(choices):
                combo.append(item_value, label)
                if item_value == selected:
                    active = index
            if choices:
                combo.set_active(active)
            combo.connect("changed", self._on_combo)
            return combo

        if definition.value_type in {"int", "float"}:
            adjustment = Gtk.Adjustment(
                value=float(value),
                lower=float(definition.minimum if definition.minimum is not None else 0),
                upper=float(definition.maximum if definition.maximum is not None else 10_000),
                step_increment=float(definition.step if definition.step is not None else 1),
                page_increment=float(definition.step if definition.step is not None else 1) * 5,
            )
            digits = 0 if definition.value_type == "int" else 1
            spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=digits)
            spin.set_numeric(True)
            spin.set_valign(Gtk.Align.CENTER)
            if definition.unit:
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.pack_start(spin, False, False, 0)
                unit = Gtk.Label(label=definition.unit)
                unit.get_style_context().add_class("settings-row-unit")
                box.pack_start(unit, False, False, 0)
                spin.connect("value-changed", self._on_spin)
                return box
            spin.connect("value-changed", self._on_spin)
            return spin

        if definition.value_type != "path":
            entry = Gtk.Entry()
            entry.set_text(str(value))
            entry.set_width_chars(22)
            entry.get_style_context().add_class("settings-entry")
            entry.connect("activate", self._on_entry)
            entry.connect("focus-out-event", self._on_entry_focus_out)
            return entry

        from ...ui.profile_images import is_profile_image_setting

        # Profile photos live at fixed assets/usuario|pc paths — no path entry.
        if is_profile_image_setting(definition.key):
            browse = Gtk.Button(label="Elegir…")
            browse.set_tooltip_text("Seleccionar imagen")
            browse.get_style_context().add_class("settings-browse-button")
            browse.connect("clicked", lambda _btn: self._on_browse_profile_image())
            return browse

        entry = Gtk.Entry()
        entry.set_text(str(value))
        entry.set_width_chars(22)
        entry.get_style_context().add_class("settings-entry")
        entry.connect("activate", self._on_entry)
        entry.connect("focus-out-event", self._on_entry_focus_out)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(entry, True, True, 0)
        browse = Gtk.Button(label="…")
        browse.set_tooltip_text("Seleccionar archivo")
        browse.get_style_context().add_class("settings-browse-button")
        browse.connect("clicked", lambda _btn: self._on_browse(entry))
        box.pack_start(browse, False, False, 0)
        self._path_entry = entry
        return box

    def _on_browse_profile_image(self) -> None:
        from ...ui.image_files import choose_image_path
        from ...ui.profile_images import install_profile_image

        parent = self.get_toplevel()
        window = parent if isinstance(parent, Gtk.Window) else None
        title = f"Seleccionar — {self._definition.label}"
        path = choose_image_path(window, title=title)
        if path is None:
            return
        try:
            installed = install_profile_image(path, self._definition.key)
        except OSError as error:
            print(f"shell: profile image install failed: {error}", flush=True)
            return
        self._on_change(self._definition.key, str(installed))

    def _on_browse(self, entry: Gtk.Entry) -> None:
        from ...ui.image_files import choose_file_path, choose_image_path

        parent = self.get_toplevel()
        window = parent if isinstance(parent, Gtk.Window) else None
        title = f"Seleccionar — {self._definition.label}"
        if self._definition.key == "sddm.background_path":
            path = choose_image_path(window, title=title)
        else:
            path = choose_file_path(window, title=title)
        if path is None:
            return
        entry.set_text(str(path))
        self._on_change(self._definition.key, str(path))

    def _on_switch(self, switch: Gtk.Switch, *_args) -> None:
        if self._suppress:
            return
        self._on_change(self._definition.key, switch.get_active())

    def _on_combo(self, combo: Gtk.ComboBoxText) -> None:
        if self._suppress:
            return
        value = combo.get_active_id()
        if value is not None:
            if value == "__system__":
                value = ""
            self._on_change(self._definition.key, value)

    def _on_spin(self, spin: Gtk.SpinButton) -> None:
        if self._suppress:
            return
        if self._definition.value_type == "int":
            self._on_change(self._definition.key, int(spin.get_value()))
        else:
            self._on_change(self._definition.key, float(spin.get_value()))

    def _on_entry(self, entry: Gtk.Entry) -> None:
        if self._suppress:
            return
        self._on_change(self._definition.key, entry.get_text().strip())

    def _on_entry_focus_out(self, entry: Gtk.Entry, _event) -> bool:
        self._on_entry(entry)
        return False
