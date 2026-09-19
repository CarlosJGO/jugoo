"""Category page builders for the Settings Center."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...settings.layout_model import parse_layout
from ...settings.manager import SettingsManager
from ...settings.profile_model import (
    MAX_PROFILE_FIELDS,
    ProfileField,
    parse_profile_fields,
    serialize_profile_fields,
)
from ...settings.schema import CATEGORY_META, CategoryId
from ...settings.task_taxonomy import (
    MAX_TASK_CATEGORIES,
    MAX_TASK_PRIORITIES,
    TaskCategory,
    TaskPriority,
    new_category_id,
    new_priority_id,
    parse_categories,
    parse_priorities,
    serialize_categories,
    serialize_priorities,
)
from ...ui.workspace_accents import (
    DEFAULT_ACCENT_COLORS,
    discover_accent_keys,
    display_label_for_key,
    normalize_hex,
    parse_accent_colors,
    resolve_accent_color,
    serialize_accent_colors,
)
from .controls import SettingRow
from .font_picker import FontFamilyPicker

_CUSTOM_EDITOR_KEYS = frozenset(
    {
        "apariencia.ui_font",
        "general.profile_fields_json",
        "widgets.workspace_accent_colors_json",
        "widgets.task_categories_json",
        "widgets.task_priorities_json",
    }
)


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
        if definition.key in _CUSTOM_EDITOR_KEYS:
            if definition.section and definition.section != current_section:
                current_section = definition.section
                section = Gtk.Label(label=current_section, xalign=0)
                section.get_style_context().add_class("settings-section")
                page.pack_start(section, False, False, 0)
            if definition.key == "apariencia.ui_font":
                page.pack_start(
                    FontFamilyPicker(
                        definition,
                        manager.get(definition.key),
                        choices=manager.choices_for(definition.key),
                        on_change=on_change,
                        apply_label=manager.apply_mode_label(definition.apply),
                    ),
                    False,
                    False,
                    0,
                )
            elif definition.key == "general.profile_fields_json":
                page.pack_start(_profile_fields_editor(manager, on_change), False, False, 0)
            elif definition.key == "widgets.workspace_accent_colors_json":
                page.pack_start(_workspace_accent_colors_editor(manager, on_change), False, False, 0)
            elif definition.key == "widgets.task_categories_json":
                page.pack_start(_task_categories_editor(manager, on_change), False, False, 0)
            elif definition.key == "widgets.task_priorities_json":
                page.pack_start(_task_priorities_editor(manager, on_change), False, False, 0)
            continue
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
        page.pack_start(_sddm_actions_editor(manager), False, False, 0)

    return page


def _sddm_actions_editor(manager: SettingsManager) -> Gtk.Widget:
    """Explicit Aplicar / Restaurar — privileged SDDM changes never auto-fire."""
    from gi.repository import GLib

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("settings-sddm-actions")

    status = Gtk.Label(xalign=0)
    status.get_style_context().add_class("settings-page-note")
    status.set_line_wrap(True)
    status.set_max_width_chars(52)

    def refresh_status(*_args) -> None:
        try:
            from ...servicios.sddm import SddmService

            snap = SddmService().status(
                {"sddm.enabled": manager.get("sddm.enabled")}
            )
            status.set_text(snap.message)
        except Exception as error:  # noqa: BLE001
            status.set_text(f"SDDM: {error}")

    refresh_status()
    box.pack_start(status, False, False, 0)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.set_halign(Gtk.Align.START)

    apply_btn = Gtk.Button(label="Aplicar SDDM")
    apply_btn.set_tooltip_text(
        "Instala/activa el tema Jugoo según las opciones (pide privilegios)."
    )
    apply_btn.get_style_context().add_class("settings-browse-button")

    restore_btn = Gtk.Button(label="Restaurar SDDM")
    restore_btn.set_tooltip_text(
        "Vuelve al tema SDDM anterior y deja de administrarlo."
    )
    restore_btn.get_style_context().add_class("settings-browse-button")

    def set_busy(busy: bool, message: str | None = None) -> None:
        apply_btn.set_sensitive(not busy)
        restore_btn.set_sensitive(not busy)
        if message is not None:
            status.set_text(message)

    def on_done(result, restore: bool = False) -> bool:
        if restore and result.ok:
            manager.set("sddm.enabled", False)
        set_busy(False)
        status.set_text(result.message if result.ok else f"Error: {result.message}")
        if result.ok:
            refresh_status()
            if result.effective_current:
                status.set_text(
                    f"{result.message} (actual: {result.effective_current})"
                )
        return False

    def on_apply(_button: Gtk.Button) -> None:
        from ...servicios.sddm import SddmService

        service = SddmService()
        set_busy(True, "Aplicando SDDM… espera el diálogo de autenticación.")
        started = service.apply_async(
            service.settings_snapshot(manager.get),
            lambda result: GLib.idle_add(on_done, result, False),
        )
        if not started:
            set_busy(False)

    def on_restore(_button: Gtk.Button) -> None:
        from ...servicios.sddm import SddmService

        set_busy(True, "Restaurando SDDM… espera el diálogo de autenticación.")
        started = SddmService().restore_async(
            lambda result: GLib.idle_add(on_done, result, True),
        )
        if not started:
            set_busy(False)

    apply_btn.connect("clicked", on_apply)
    restore_btn.connect("clicked", on_restore)
    row.pack_start(apply_btn, False, False, 0)
    row.pack_start(restore_btn, False, False, 0)
    box.pack_start(row, False, False, 0)

    hint = Gtk.Label(
        label=(
            "Los controles de arriba solo guardan preferencias. "
            "Aplicar pide autenticación (Polkit) en segundo plano; "
            "la shell no se bloquea."
        ),
        xalign=0,
    )
    hint.get_style_context().add_class("settings-row-hint")
    hint.set_line_wrap(True)
    box.pack_start(hint, False, False, 0)
    return box


def _profile_fields_editor(
    manager: SettingsManager,
    on_change: Callable[[str, object], None],
) -> Gtk.Widget:
    """Editable title/value pairs persisted as ``general.profile_fields_json``."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("settings-profile-fields")

    hint = Gtk.Label(
        label="Agrega datos propios (universidad, redes, etc.). Se muestran en el panel izquierdo.",
        xalign=0,
    )
    hint.get_style_context().add_class("settings-row-desc")
    hint.set_line_wrap(True)
    box.pack_start(hint, False, False, 0)

    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.pack_start(list_box, False, False, 0)

    def _current_fields() -> list[ProfileField]:
        return list(parse_profile_fields(str(manager.get("general.profile_fields_json") or "")))

    def _persist(fields: list[ProfileField]) -> None:
        on_change("general.profile_fields_json", serialize_profile_fields(fields))

    def _rebuild() -> None:
        for child in list(list_box.get_children()):
            list_box.remove(child)
            child.destroy()
        fields = _current_fields()
        for index, field in enumerate(fields):
            list_box.pack_start(_field_row(index, field, fields), False, False, 0)
        list_box.show_all()
        add_button.set_sensitive(len(fields) < MAX_PROFILE_FIELDS)

    def _field_row(index: int, field: ProfileField, fields: list[ProfileField]) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("settings-profile-field-row")

        title_entry = Gtk.Entry()
        title_entry.set_placeholder_text("Título")
        title_entry.set_text(field.title)
        title_entry.set_width_chars(12)
        title_entry.get_style_context().add_class("settings-entry")
        row.pack_start(title_entry, True, True, 0)

        value_entry = Gtk.Entry()
        value_entry.set_placeholder_text("Valor")
        value_entry.set_text(field.value)
        value_entry.set_width_chars(16)
        value_entry.get_style_context().add_class("settings-entry")
        row.pack_start(value_entry, True, True, 0)

        def _commit(*_args) -> None:
            updated = _current_fields()
            if index >= len(updated):
                return
            updated[index] = ProfileField(
                title=title_entry.get_text().strip() or "Dato",
                value=value_entry.get_text().strip(),
            )
            _persist(updated)

        title_entry.connect("activate", _commit)
        title_entry.connect("focus-out-event", lambda *_a: (_commit(), False)[1])
        value_entry.connect("activate", _commit)
        value_entry.connect("focus-out-event", lambda *_a: (_commit(), False)[1])

        remove = Gtk.Button(label="Eliminar")
        remove.get_style_context().add_class("settings-browse-button")

        def _remove(_btn) -> None:
            updated = _current_fields()
            if 0 <= index < len(updated):
                del updated[index]
                _persist(updated)
                _rebuild()

        remove.connect("clicked", _remove)
        row.pack_start(remove, False, False, 0)
        return row

    add_button = Gtk.Button(label="Agregar dato")
    add_button.get_style_context().add_class("settings-browse-button")
    add_button.set_halign(Gtk.Align.START)

    def _add(_btn) -> None:
        fields = _current_fields()
        if len(fields) >= MAX_PROFILE_FIELDS:
            return
        fields.append(ProfileField(title="Nuevo", value=""))
        _persist(fields)
        _rebuild()

    add_button.connect("clicked", _add)
    box.pack_start(add_button, False, False, 0)
    _rebuild()
    return box


def _workspace_accent_colors_editor(
    manager: SettingsManager,
    on_change: Callable[[str, object], None],
) -> Gtk.Widget:
    """Color editor for special/named workspaces discovered from Hypr + defaults."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("settings-task-taxonomy")

    hint = Gtk.Label(
        label=(
            "Cada workspace especial o con nombre (special, minimizados, gaming…) "
            "usa su propio color en la barra. Edita el hex y se aplica al momento."
        ),
        xalign=0,
    )
    hint.get_style_context().add_class("settings-row-desc")
    hint.set_line_wrap(True)
    box.pack_start(hint, False, False, 0)

    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.pack_start(list_box, False, False, 0)

    def _current() -> dict[str, str]:
        return parse_accent_colors(str(manager.get("widgets.workspace_accent_colors_json") or ""))

    def _persist(colors: dict[str, str]) -> None:
        # Keep defaults for known keys so reset stays meaningful.
        merged = {**DEFAULT_ACCENT_COLORS, **colors}
        on_change("widgets.workspace_accent_colors_json", serialize_accent_colors(merged))

    def _keys() -> tuple[str, ...]:
        live_names: list[str] = []
        try:
            import json
            import subprocess

            result = subprocess.run(
                ["hyprctl", "workspaces", "-j"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout)
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and item.get("name") is not None:
                            live_names.append(str(item["name"]))
        except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired, ValueError):
            pass
        overrides = _current()
        return discover_accent_keys(live_names=tuple(live_names) + tuple(overrides))

    def _rebuild() -> None:
        for child in list(list_box.get_children()):
            list_box.remove(child)
            child.destroy()
        overrides = _current()
        for key in _keys():
            list_box.pack_start(_color_row(key, overrides), False, False, 0)
        list_box.show_all()

    def _color_row(key: str, overrides: dict[str, str]) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("settings-task-taxonomy-row")

        swatch = Gtk.EventBox()
        swatch.set_size_request(18, 18)
        swatch.set_valign(Gtk.Align.CENTER)
        color = resolve_accent_color(key, {**DEFAULT_ACCENT_COLORS, **overrides})
        css = Gtk.CssProvider()
        css.load_from_data(
            f"eventbox {{ background-color: {color}; border-radius: 4px; }}".encode("utf-8")
        )
        swatch.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        row.pack_start(swatch, False, False, 0)

        label = Gtk.Label(label=display_label_for_key(key), xalign=0)
        label.set_hexpand(True)
        label.get_style_context().add_class("settings-row-title")
        row.pack_start(label, True, True, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("#RRGGBB")
        entry.set_text(color)
        entry.set_width_chars(9)
        entry.get_style_context().add_class("settings-entry")
        row.pack_start(entry, False, False, 0)

        def _commit(*_args) -> None:
            updated = _current()
            normalized = normalize_hex(entry.get_text())
            if normalized is None:
                entry.set_text(resolve_accent_color(key, {**DEFAULT_ACCENT_COLORS, **updated}))
                return
            updated[key] = normalized
            _persist(updated)
            _rebuild()

        entry.connect("activate", _commit)
        entry.connect("focus-out-event", lambda *_a: (_commit(), False)[1])
        return row

    _rebuild()
    return box


def _task_categories_editor(
    manager: SettingsManager,
    on_change: Callable[[str, object], None],
) -> Gtk.Widget:
    """Friendly list editor for widgets.task_categories_json."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("settings-task-taxonomy")

    hint = Gtk.Label(
        label="Categorías para organizar tareas (universidad, trabajo…). Se eligen al crear una tarea.",
        xalign=0,
    )
    hint.get_style_context().add_class("settings-row-desc")
    hint.set_line_wrap(True)
    box.pack_start(hint, False, False, 0)

    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.pack_start(list_box, False, False, 0)

    def _current() -> list[TaskCategory]:
        return list(parse_categories(str(manager.get("widgets.task_categories_json") or "")))

    def _persist(items: list[TaskCategory]) -> None:
        on_change("widgets.task_categories_json", serialize_categories(items))

    def _rebuild() -> None:
        for child in list(list_box.get_children()):
            list_box.remove(child)
            child.destroy()
        items = _current()
        for index, item in enumerate(items):
            list_box.pack_start(_category_row(index, item, items), False, False, 0)
        list_box.show_all()
        add_button.set_sensitive(len(items) < MAX_TASK_CATEGORIES)

    def _category_row(index: int, item: TaskCategory, items: list[TaskCategory]) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("settings-task-taxonomy-row")

        label_entry = Gtk.Entry()
        label_entry.set_placeholder_text("Nombre")
        label_entry.set_text(item.label)
        label_entry.get_style_context().add_class("settings-entry")
        row.pack_start(label_entry, True, True, 0)

        color_entry = Gtk.Entry()
        color_entry.set_placeholder_text("#RRGGBB")
        color_entry.set_text(item.color)
        color_entry.set_width_chars(9)
        color_entry.get_style_context().add_class("settings-entry")
        row.pack_start(color_entry, False, False, 0)

        def _commit(*_args) -> None:
            updated = _current()
            if index >= len(updated):
                return
            updated[index] = TaskCategory(
                id=updated[index].id,
                label=label_entry.get_text().strip() or "Categoría",
                color=color_entry.get_text().strip(),
            )
            _persist(updated)

        label_entry.connect("activate", _commit)
        label_entry.connect("focus-out-event", lambda *_a: (_commit(), False)[1])
        color_entry.connect("activate", _commit)
        color_entry.connect("focus-out-event", lambda *_a: (_commit(), False)[1])

        up = Gtk.Button(label="↑")
        up.get_style_context().add_class("settings-browse-button")
        up.set_sensitive(index > 0)

        def _move_up(_btn) -> None:
            updated = _current()
            if index <= 0 or index >= len(updated):
                return
            updated[index - 1], updated[index] = updated[index], updated[index - 1]
            _persist(updated)
            _rebuild()

        up.connect("clicked", _move_up)
        row.pack_start(up, False, False, 0)

        down = Gtk.Button(label="↓")
        down.get_style_context().add_class("settings-browse-button")
        down.set_sensitive(index < len(items) - 1)

        def _move_down(_btn) -> None:
            updated = _current()
            if index >= len(updated) - 1:
                return
            updated[index + 1], updated[index] = updated[index], updated[index + 1]
            _persist(updated)
            _rebuild()

        down.connect("clicked", _move_down)
        row.pack_start(down, False, False, 0)

        remove = Gtk.Button(label="Eliminar")
        remove.get_style_context().add_class("settings-browse-button")

        def _remove(_btn) -> None:
            updated = _current()
            if 0 <= index < len(updated):
                del updated[index]
                _persist(updated)
                _rebuild()

        remove.connect("clicked", _remove)
        row.pack_start(remove, False, False, 0)
        return row

    add_button = Gtk.Button(label="Agregar categoría")
    add_button.get_style_context().add_class("settings-browse-button")
    add_button.set_halign(Gtk.Align.START)

    def _add(_btn) -> None:
        items = _current()
        if len(items) >= MAX_TASK_CATEGORIES:
            return
        label = "Nueva"
        items.append(TaskCategory(id=new_category_id(label), label=label, color=""))
        _persist(items)
        _rebuild()

    add_button.connect("clicked", _add)
    box.pack_start(add_button, False, False, 0)
    _rebuild()
    return box


def _task_priorities_editor(
    manager: SettingsManager,
    on_change: Callable[[str, object], None],
) -> Gtk.Widget:
    """Friendly list editor for widgets.task_priorities_json."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("settings-task-taxonomy")

    hint = Gtk.Label(
        label="Prioridades con peso numérico (ej. Alta = 3, Baja = 0). Mayor peso = más urgente.",
        xalign=0,
    )
    hint.get_style_context().add_class("settings-row-desc")
    hint.set_line_wrap(True)
    box.pack_start(hint, False, False, 0)

    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.pack_start(list_box, False, False, 0)

    def _current() -> list[TaskPriority]:
        return list(parse_priorities(str(manager.get("widgets.task_priorities_json") or "")))

    def _persist(items: list[TaskPriority]) -> None:
        on_change("widgets.task_priorities_json", serialize_priorities(items))

    def _rebuild() -> None:
        for child in list(list_box.get_children()):
            list_box.remove(child)
            child.destroy()
        items = _current()
        for index, item in enumerate(items):
            list_box.pack_start(_priority_row(index, item, items), False, False, 0)
        list_box.show_all()
        add_button.set_sensitive(len(items) < MAX_TASK_PRIORITIES)

    def _priority_row(index: int, item: TaskPriority, items: list[TaskPriority]) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("settings-task-taxonomy-row")

        label_entry = Gtk.Entry()
        label_entry.set_placeholder_text("Nombre")
        label_entry.set_text(item.label)
        label_entry.get_style_context().add_class("settings-entry")
        row.pack_start(label_entry, True, True, 0)

        weight_label = Gtk.Label(label="Peso", xalign=0)
        weight_label.get_style_context().add_class("settings-row-desc")
        row.pack_start(weight_label, False, False, 0)

        adjustment = Gtk.Adjustment(
            value=item.weight,
            lower=0,
            upper=99,
            step_increment=1,
            page_increment=5,
        )
        spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.set_width_chars(3)
        row.pack_start(spin, False, False, 0)

        def _commit(*_args) -> None:
            updated = _current()
            if index >= len(updated):
                return
            updated[index] = TaskPriority(
                id=updated[index].id,
                label=label_entry.get_text().strip() or "Prioridad",
                weight=int(spin.get_value()),
            )
            _persist(updated)

        label_entry.connect("activate", _commit)
        label_entry.connect("focus-out-event", lambda *_a: (_commit(), False)[1])
        spin.connect("value-changed", _commit)

        up = Gtk.Button(label="↑")
        up.get_style_context().add_class("settings-browse-button")
        up.set_sensitive(index > 0)

        def _move_up(_btn) -> None:
            updated = _current()
            if index <= 0 or index >= len(updated):
                return
            updated[index - 1], updated[index] = updated[index], updated[index - 1]
            _persist(updated)
            _rebuild()

        up.connect("clicked", _move_up)
        row.pack_start(up, False, False, 0)

        down = Gtk.Button(label="↓")
        down.get_style_context().add_class("settings-browse-button")
        down.set_sensitive(index < len(items) - 1)

        def _move_down(_btn) -> None:
            updated = _current()
            if index >= len(updated) - 1:
                return
            updated[index + 1], updated[index] = updated[index], updated[index + 1]
            _persist(updated)
            _rebuild()

        down.connect("clicked", _move_down)
        row.pack_start(down, False, False, 0)

        remove = Gtk.Button(label="Eliminar")
        remove.get_style_context().add_class("settings-browse-button")

        def _remove(_btn) -> None:
            updated = _current()
            if 0 <= index < len(updated):
                del updated[index]
                _persist(updated)
                _rebuild()

        remove.connect("clicked", _remove)
        row.pack_start(remove, False, False, 0)
        return row

    add_button = Gtk.Button(label="Agregar prioridad")
    add_button.get_style_context().add_class("settings-browse-button")
    add_button.set_halign(Gtk.Align.START)

    def _add(_btn) -> None:
        items = _current()
        if len(items) >= MAX_TASK_PRIORITIES:
            return
        label = "Nueva"
        items.append(TaskPriority(id=new_priority_id(label), label=label, weight=0))
        _persist(items)
        _rebuild()

    add_button.connect("clicked", _add)
    box.pack_start(add_button, False, False, 0)
    _rebuild()
    return box


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
    caption.set_line_wrap(True)
    box.pack_start(caption, False, False, 0)

    canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    canvas.get_style_context().add_class("settings-layout-canvas")

    for region, label in (("left", "Izquierda"), ("center", "Centro"), ("right", "Derecha")):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        region_label = Gtk.Label(label=label, xalign=0)
        region_label.get_style_context().add_class("settings-row-unit")
        region_label.set_width_chars(10)
        region_label.set_valign(Gtk.Align.START)
        row.pack_start(region_label, False, False, 0)
        chips = Gtk.FlowBox()
        chips.get_style_context().add_class("settings-layout-chips")
        chips.set_selection_mode(Gtk.SelectionMode.NONE)
        chips.set_homogeneous(False)
        chips.set_min_children_per_line(1)
        chips.set_max_children_per_line(8)
        chips.set_column_spacing(6)
        chips.set_row_spacing(6)
        chips.set_halign(Gtk.Align.START)
        chips.set_hexpand(True)
        for slot in sorted(
            (item for item in slots if item.region == region and item.visible),
            key=lambda item: item.order,
        ):
            chip = Gtk.Label(label=slot.id)
            chip.get_style_context().add_class("settings-layout-chip")
            chip.set_halign(Gtk.Align.START)
            chips.add(chip)
        row.pack_start(chips, True, True, 0)
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
