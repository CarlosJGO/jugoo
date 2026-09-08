"""Category page builders for the Settings Center."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...settings.layout_model import parse_layout
from ...settings.manager import SettingsManager
from ...settings.schema import CATEGORY_META, CategoryId
from .controls import SettingRow


def build_category_page(
    manager: SettingsManager,
    category: CategoryId,
    *,
    on_change: Callable[[str, object], None],
) -> Gtk.Widget:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    page.get_style_context().add_class("settings-page")

    title, subtitle = CATEGORY_META[category]
    heading = Gtk.Label(label=title, xalign=0)
    heading.get_style_context().add_class("settings-page-title")
    page.pack_start(heading, False, False, 0)

    sub = Gtk.Label(label=subtitle, xalign=0)
    sub.get_style_context().add_class("settings-page-subtitle")
    page.pack_start(sub, False, False, 0)

    if category is CategoryId.GENERAL:
        page.pack_start(_general_intro(manager), False, False, 0)

    if category is CategoryId.MODO_NOCHE:
        page.pack_start(_night_status_banner(manager), False, False, 0)

    if category is CategoryId.LAYOUT:
        page.pack_start(_layout_preview(manager), False, False, 0)

    settings = manager.settings_for(category)
    if not settings and category is CategoryId.GENERAL:
        return page

    current_section = None
    for definition in settings:
        if definition.section and definition.section != current_section:
            current_section = definition.section
            section = Gtk.Label(label=current_section, xalign=0)
            section.get_style_context().add_class("settings-section")
            page.pack_start(section, False, False, 0)
        choices = manager.choices_for(definition.key)
        row = SettingRow(
            definition,
            manager.get(definition.key),
            choices=choices,
            on_change=on_change,
            apply_label=manager.apply_mode_label(definition.apply),
        )
        page.pack_start(row, False, False, 0)

    if category is CategoryId.TEMA:
        note = Gtk.Label(
            label=(
                "El tema define la identidad visual. Las preferencias de "
                "comportamiento viven en otras categorías."
            ),
            xalign=0,
        )
        note.get_style_context().add_class("settings-page-note")
        note.set_line_wrap(True)
        page.pack_start(note, False, False, 0)

    return page


def _general_intro(manager: SettingsManager) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.get_style_context().add_class("settings-intro")
    path = Gtk.Label(label=f"Archivo: {manager.path}", xalign=0)
    path.get_style_context().add_class("settings-row-desc")
    path.set_selectable(True)
    box.pack_start(path, False, False, 0)
    tip = Gtk.Label(
        label=(
            "Los cambios se guardan al instante. Las opciones marcadas "
            "requieren recarga o reinicio de Jugoo."
        ),
        xalign=0,
    )
    tip.get_style_context().add_class("settings-row-desc")
    tip.set_line_wrap(True)
    box.pack_start(tip, False, False, 0)
    return box


def _night_status_banner(manager: SettingsManager) -> Gtk.Widget:
    status = manager.night_mode.apply()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.get_style_context().add_class("settings-banner")
    backend = status.backend or "ninguno"
    line = Gtk.Label(
        label=f"Backend: {backend} — {status.message}",
        xalign=0,
    )
    line.get_style_context().add_class("settings-row-desc")
    line.set_line_wrap(True)
    box.pack_start(line, False, False, 0)
    if status.backend is None:
        help_label = Gtk.Label(
            label="En CachyOS/Hyprland: sudo pacman -S hyprsunset",
            xalign=0,
        )
        help_label.get_style_context().add_class("settings-row-hint")
        box.pack_start(help_label, False, False, 0)
    return box


def _layout_preview(manager: SettingsManager) -> Gtk.Widget:
    slots = parse_layout(str(manager.get("layout.modules_json")))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("settings-layout-preview")

    caption = Gtk.Label(
        label="Previsualización del modelo de layout (editor visual futuro)",
        xalign=0,
    )
    caption.get_style_context().add_class("settings-section")
    box.pack_start(caption, False, False, 0)

    canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    canvas.get_style_context().add_class("settings-layout-canvas")

    for region, label in (("left", "Izquierda"), ("center", "Centro"), ("right", "Derecha")):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        region_label = Gtk.Label(label=label, xalign=0)
        region_label.get_style_context().add_class("settings-row-unit")
        region_label.set_width_chars(10)
        row.pack_start(region_label, False, False, 0)
        for slot in sorted(
            (item for item in slots if item.region == region and item.visible),
            key=lambda item: item.order,
        ):
            chip = Gtk.Label(label=slot.id)
            chip.get_style_context().add_class("settings-layout-chip")
            row.pack_start(chip, False, False, 0)
        canvas.pack_start(row, False, False, 0)

    box.pack_start(canvas, False, False, 0)
    note = Gtk.Label(
        label=(
            "El orden y la visibilidad ya viven en un modelo serializable. "
            "El editor visual podrá mover estos slots sin redescribir la barra."
        ),
        xalign=0,
    )
    note.get_style_context().add_class("settings-row-desc")
    note.set_line_wrap(True)
    box.pack_start(note, False, False, 0)
    return box
