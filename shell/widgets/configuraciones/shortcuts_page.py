"""Read-only keyboard shortcuts page for Configuraciones → Atajos."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ...servicios.atajos import (
    SOURCE_LABELS,
    ShortcutEntry,
    ShortcutSnapshot,
    filter_entries,
    group_by_category,
    load_shortcuts,
)


def build_shortcuts_page() -> Gtk.Widget:
    """Build the Atajos viewer. Reloads from the registry on demand."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    page.get_style_context().add_class("settings-page")
    page.get_style_context().add_class("settings-shortcuts-page")

    heading = Gtk.Label(label="Atajos", xalign=0)
    heading.get_style_context().add_class("settings-page-title")
    page.pack_start(heading, False, False, 0)

    sub = Gtk.Label(
        label="Atajos activos del sistema. Puedes gestionar atajos de aplicaciones.",
        xalign=0,
    )
    sub.get_style_context().add_class("settings-page-subtitle")
    page.pack_start(sub, False, False, 0)

    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.get_style_context().add_class("settings-shortcuts-toolbar")

    search = Gtk.SearchEntry()
    search.set_placeholder_text("Buscar…")
    search.set_hexpand(True)
    toolbar.pack_start(search, True, True, 0)

    category_combo = Gtk.ComboBoxText()
    category_combo.get_style_context().add_class("settings-combo")
    category_combo.append("", "Todas las categorías")
    category_combo.set_active_id("")
    toolbar.pack_start(category_combo, False, False, 0)

    manage_btn = Gtk.Button(label="Gestionar")
    manage_btn.get_style_context().add_class("settings-browse-button")
    manage_btn.set_tooltip_text("Añadir o editar atajos de aplicaciones")
    toolbar.pack_end(manage_btn, False, False, 0)

    refresh_btn = Gtk.Button(label="Actualizar")
    refresh_btn.get_style_context().add_class("settings-browse-button")
    refresh_btn.set_tooltip_text("Volver a leer los atajos activos")
    toolbar.pack_end(refresh_btn, False, False, 0)
    page.pack_start(toolbar, False, False, 0)

    status = Gtk.Label(xalign=0)
    status.get_style_context().add_class("settings-page-note")
    status.set_line_wrap(True)
    page.pack_start(status, False, False, 0)

    list_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    list_host.get_style_context().add_class("settings-shortcuts-list")
    page.pack_start(list_host, True, True, 0)

    state: dict[str, object] = {
        "snapshot": ShortcutSnapshot(entries=()),
        "query": "",
        "category": "",
    }

    def reload_data(*_args) -> None:
        snapshot = load_shortcuts()
        state["snapshot"] = snapshot
        _refill_categories(category_combo, snapshot)
        render()

    def render(*_args) -> None:
        snapshot = state["snapshot"]
        assert isinstance(snapshot, ShortcutSnapshot)
        query = str(state.get("query") or "")
        category = str(state.get("category") or "") or None
        filtered = filter_entries(
            snapshot.entries,
            query=query,
            category=category,
        )
        _rebuild_list(list_host, filtered)
        status.set_text(_status_text(snapshot, filtered))

    def on_search(entry: Gtk.SearchEntry) -> None:
        state["query"] = entry.get_text() or ""
        render()

    def on_category(combo: Gtk.ComboBoxText) -> None:
        state["category"] = combo.get_active_id() or ""
        render()

    search.connect("search-changed", on_search)
    category_combo.connect("changed", on_category)
    refresh_btn.connect("clicked", reload_data)

    def on_manage(*_args) -> None:
        from .app_shortcuts_dialog import open_app_shortcuts_manager

        open_app_shortcuts_manager(page, on_changed=reload_data)

    manage_btn.connect("clicked", on_manage)

    reload_data()
    return page


def _refill_categories(combo: Gtk.ComboBoxText, snapshot: ShortcutSnapshot) -> None:
    current = combo.get_active_id() or ""
    # Clear existing ids except the "all" sentinel.
    model = combo.get_model()
    if model is not None:
        while len(model) > 1:
            combo.remove(1)
    seen: list[str] = []
    for entry in snapshot.entries:
        if entry.category not in seen:
            seen.append(entry.category)
            combo.append(entry.category, entry.category)
    if current and any(entry.category == current for entry in snapshot.entries):
        combo.set_active_id(current)
    else:
        combo.set_active_id("")


def _rebuild_list(host: Gtk.Box, entries: tuple[ShortcutEntry, ...]) -> None:
    for child in list(host.get_children()):
        host.remove(child)
        child.destroy()

    if not entries:
        empty = Gtk.Label(
            label="No hay atajos para mostrar.",
            xalign=0,
        )
        empty.get_style_context().add_class("settings-page-note")
        host.pack_start(empty, False, False, 0)
        host.show_all()
        return

    for category, group in group_by_category(entries):
        section = Gtk.Label(label=category, xalign=0)
        section.get_style_context().add_class("settings-section")
        host.pack_start(section, False, False, 0)

        for entry in group:
            host.pack_start(_shortcut_row(entry), False, False, 0)

    host.show_all()


def _shortcut_row(entry: ShortcutEntry) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.get_style_context().add_class("settings-row")
    row.get_style_context().add_class("settings-shortcut-row")

    keys = Gtk.Label(label=entry.keys, xalign=0)
    keys.get_style_context().add_class("settings-shortcut-keys")
    keys.set_width_chars(18)
    keys.set_xalign(0.0)
    row.pack_start(keys, False, False, 0)

    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    texts.set_hexpand(True)
    title = Gtk.Label(label=entry.description, xalign=0)
    title.get_style_context().add_class("settings-row-title")
    title.set_line_wrap(True)
    texts.pack_start(title, False, False, 0)

    source_label = SOURCE_LABELS.get(entry.source, entry.source)
    meta_bits = [entry.category, source_label]
    meta = Gtk.Label(label=" · ".join(part for part in meta_bits if part), xalign=0)
    meta.get_style_context().add_class("settings-shortcut-source")
    texts.pack_start(meta, False, False, 0)

    if entry.note:
        note = Gtk.Label(label=entry.note, xalign=0)
        note.get_style_context().add_class("settings-shortcut-note")
        note.set_line_wrap(True)
        texts.pack_start(note, False, False, 0)

    row.pack_start(texts, True, True, 0)
    return row


def _status_text(snapshot: ShortcutSnapshot, filtered: tuple[ShortcutEntry, ...]) -> str:
    total = len(snapshot.entries)
    shown = len(filtered)
    if snapshot.source_note == "unavailable":
        base = "Hyprland no disponible y no se encontró configuración de binds."
    elif snapshot.source_note in {"conf", "conf-fallback"}:
        base = f"Mostrando binds desde hyprland.conf ({shown} de {total})."
    else:
        base = f"{shown} de {total} atajos activos."
    if snapshot.duplicates:
        base += f" Aviso: {len(snapshot.duplicates)} combinación(es) duplicada(s)."
    return base
