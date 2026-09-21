"""Anchored tasks panel with Hoy / Completadas / Por fecha views + AIDYC source."""

from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GLib", "2.0")

from gi.repository import GLib, Gtk, Pango

from ...config import TASKS_POPUP_MAX_HEIGHT, TASKS_POPUP_OFFSET, TASKS_POPUP_WIDTH
from ...models import TASK_STATUS_COMPLETED, TASK_STATUS_MISSED, TaskSnapshot
from ...popup_handle import hide_popup, pointer_inside_widget, present_popup
from ...popup_spawn import publish_popup_spawn
from ...servicios.tareas.aidyc_client import AidycConflictError
from ...servicios.tareas.aidyc_mapping import assignee_label
from ...servicios.tareas.aidyc_provider import (
    AidycTaskProvider,
    build_aidyc_provider_from_settings,
)
from ...servicios.tareas.logic import format_day_label, meta_label, parse_iso_date
from ...servicios.tareas.tasks import TasksService
from ...settings.task_taxonomy import (
    category_label_map,
    parse_categories,
    parse_priorities,
    priority_label_map,
    priority_weight_map,
    sort_by_priority,
)
from ...ui.starfield import install_starfield, resolve_event_bus
from ...window_identity import (
    TITLE_TASKS,
    configure_interactive_popup,
    configure_toplevel,
    position_popup_below_anchor,
    register_shell_popup,
    schedule_popup_position,
)
from .composer import TaskComposer
from .task_row import TaskRow

_TAB_TODAY = "hoy"
_TAB_DONE = "completadas"
_TAB_BY_DATE = "por_fecha"
_SOURCE_LOCAL = "local"
_SOURCE_AIDYC = "aidyc"


class TasksPopup(Gtk.Window):
    """Interactive task list anchored below the bar button."""

    def __init__(self, shell_window: Gtk.Window, tasks_service: TasksService) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._shell_window = shell_window
        self._service = tasks_service
        self._aidyc: AidycTaskProvider | None = None
        self._aidyc_poll_id = 0
        self._aidyc_users: list[dict] = []
        self._anchor_button: Gtk.Widget | None = None
        self._fixed_popup_top: int | None = None
        self._last_height = 0
        self._active_tab = _TAB_TODAY
        self._active_source = _SOURCE_LOCAL
        self._browse_date = date.today()
        self._category_labels: dict[str, str] = {}
        self._priority_labels: dict[str, str] = {}
        self._priority_weights: dict[str, int] = {}
        self._status_label = Gtk.Label(label="", xalign=0)
        self._status_label.get_style_context().add_class("tasks-popup-status")
        self._status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._status_label.set_no_show_all(True)
        self._status_label.hide()

        self.set_name("shell-tasks")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_TASKS)
        configure_interactive_popup(self)
        self.set_default_size(TASKS_POPUP_WIDTH, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("tasks-popup-content")
        outer.set_size_request(TASKS_POPUP_WIDTH, -1)
        install_starfield(
            self,
            outer,
            resolve_event_bus(shell_window),
            corner_radius=16.0,
        )

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("tasks-popup-header")
        title = Gtk.Label(label="Tareas", xalign=0)
        title.get_style_context().add_class("tasks-popup-title")
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)
        self._sync_button = Gtk.Button(label="Sync", relief=Gtk.ReliefStyle.NONE)
        self._sync_button.get_style_context().add_class("tasks-popup-add")
        self._sync_button.set_tooltip_text("Sincronizar AIDYC")
        self._sync_button.connect("clicked", self._on_sync_clicked)
        self._sync_button.set_no_show_all(True)
        self._sync_button.hide()
        header.pack_start(self._sync_button, False, False, 0)
        self._add_button = Gtk.Button(label="Nueva", relief=Gtk.ReliefStyle.NONE)
        self._add_button.get_style_context().add_class("tasks-popup-add")
        self._add_button.connect("clicked", self._on_toggle_composer)
        header.pack_start(self._add_button, False, False, 0)
        outer.pack_start(header, False, False, 0)

        sources = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sources.get_style_context().add_class("tasks-popup-tabs")
        self._source_buttons: dict[str, Gtk.ToggleButton] = {}
        for key, label in ((_SOURCE_LOCAL, "Locales"), (_SOURCE_AIDYC, "AIDYC")):
            button = Gtk.ToggleButton(label=label)
            button.get_style_context().add_class("tasks-popup-tab")
            button.connect("toggled", self._on_source_toggled, key)
            sources.pack_start(button, True, True, 0)
            self._source_buttons[key] = button
        self._source_buttons[_SOURCE_LOCAL].set_active(True)
        outer.pack_start(sources, False, False, 0)
        outer.pack_start(self._status_label, False, False, 0)

        self._composer = TaskComposer(
            on_submit=self._on_composer_submit,
            on_cancel=self._hide_composer,
        )
        self._composer.set_no_show_all(True)
        self._composer.hide()
        outer.pack_start(self._composer, False, False, 0)

        self._assignee_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._assignee_row.get_style_context().add_class("task-composer")
        assign_label = Gtk.Label(label="Asignar", xalign=0)
        assign_label.get_style_context().add_class("task-composer-label")
        self._assignee_combo = Gtk.ComboBoxText()
        self._assignee_combo.append("", "Sin asignar / yo")
        self._assignee_combo.set_active_id("")
        self._assignee_row.pack_start(assign_label, False, False, 0)
        self._assignee_row.pack_start(self._assignee_combo, True, True, 0)
        self._assignee_row.set_no_show_all(True)
        self._assignee_row.hide()
        outer.pack_start(self._assignee_row, False, False, 0)

        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tabs.get_style_context().add_class("tasks-popup-tabs")
        self._tab_buttons: dict[str, Gtk.ToggleButton] = {}
        for key, label in (
            (_TAB_TODAY, "Hoy"),
            (_TAB_DONE, "Completadas"),
            (_TAB_BY_DATE, "Por fecha"),
        ):
            button = Gtk.ToggleButton(label=label)
            button.get_style_context().add_class("tasks-popup-tab")
            button.connect("toggled", self._on_tab_toggled, key)
            tabs.pack_start(button, True, True, 0)
            self._tab_buttons[key] = button
        self._tab_buttons[_TAB_TODAY].set_active(True)
        self._tabs_box = tabs
        outer.pack_start(tabs, False, False, 0)

        self._date_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._date_bar.get_style_context().add_class("tasks-popup-date-bar")
        self._date_entry = Gtk.Entry()
        self._date_entry.set_placeholder_text("AAAA-MM-DD")
        self._date_entry.set_text(self._browse_date.isoformat())
        self._date_entry.set_width_chars(12)
        self._date_entry.connect("activate", self._on_date_apply)
        today_btn = Gtk.Button(label="Hoy", relief=Gtk.ReliefStyle.NONE)
        today_btn.get_style_context().add_class("tasks-popup-date-chip")
        today_btn.connect("clicked", self._on_date_today)
        apply_btn = Gtk.Button(label="Ver", relief=Gtk.ReliefStyle.NONE)
        apply_btn.get_style_context().add_class("tasks-popup-date-chip")
        apply_btn.connect("clicked", self._on_date_apply)
        self._date_bar.pack_start(self._date_entry, True, True, 0)
        self._date_bar.pack_start(today_btn, False, False, 0)
        self._date_bar.pack_start(apply_btn, False, False, 0)
        self._date_bar.set_no_show_all(True)
        self._date_bar.hide()
        outer.pack_start(self._date_bar, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(TASKS_POPUP_MAX_HEIGHT)
        scrolled.get_style_context().add_class("tasks-popup-scroll")
        outer.pack_start(scrolled, True, True, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._list.get_style_context().add_class("tasks-popup-list")
        scrolled.add(self._list)

        self._empty = Gtk.Label(label="No hay tareas todavía")
        self._empty.get_style_context().add_class("tasks-popup-empty")
        self._empty.set_margin_top(12)
        self._empty.set_margin_bottom(12)
        self.connect("size-allocate", self._on_size_allocate)
        self._reload_taxonomy()

    def open_for(self, anchor_button: Gtk.Widget) -> None:
        self._anchor_button = anchor_button
        self._fixed_popup_top = None
        self._last_height = 0
        self._hide_composer()
        self._reload_taxonomy()
        self._ensure_aidyc_provider()
        if self._active_source == _SOURCE_AIDYC:
            self._pull_aidyc_async()
        self.refresh()
        publish_popup_spawn(
            self,
            anchor_button,
            title=TITLE_TASKS,
            offset=TASKS_POPUP_OFFSET,
        )
        present_popup(self)
        schedule_popup_position(self._position_after_show)
        self._start_aidyc_poll()

    def close_popup(self) -> None:
        self._stop_aidyc_poll()
        self._anchor_button = None
        self._fixed_popup_top = None
        self._last_height = 0
        self._hide_composer()
        hide_popup(self)

    def pointer_is_inside(self) -> bool:
        return pointer_inside_widget(self)

    def refresh(self) -> None:
        for child in self._list.get_children():
            self._list.remove(child)

        self._reload_taxonomy()
        aidyc_mode = self._active_source == _SOURCE_AIDYC
        self._sync_button.set_no_show_all(not aidyc_mode)
        if aidyc_mode:
            self._sync_button.show()
        else:
            self._sync_button.hide()
        self._tabs_box.set_sensitive(not aidyc_mode)
        self._update_status_banner()

        if aidyc_mode:
            self._date_bar.hide()
            self._fill_aidyc()
        else:
            show_date = self._active_tab == _TAB_BY_DATE
            self._date_bar.set_no_show_all(not show_date)
            if show_date:
                self._date_bar.show_all()
            else:
                self._date_bar.hide()
            if self._active_tab == _TAB_DONE:
                self._fill_completed()
            elif self._active_tab == _TAB_BY_DATE:
                self._fill_by_date()
            else:
                self._fill_today()
        self._list.show_all()
        self.queue_resize()
        self._reposition()

    def _fill_today(self) -> None:
        today = date.today()
        board = list(self._service.snapshot.tasks)
        upcoming = list(self._service.upcoming())
        if not self._service.records():
            self._list.pack_start(self._empty, False, False, 0)
            self._empty.set_text("No hay tareas todavía")
            self._empty.show()
            return

        overdue = sort_by_priority(
            [item for item in board if item.status == "overdue"],
            self._priority_weights,
        )
        today_items = [item for item in board if item.status != "overdue"]
        done_today = [item for item in today_items if item.status == TASK_STATUS_COMPLETED]
        open_today = sort_by_priority(
            [item for item in today_items if item.status != TASK_STATUS_COMPLETED],
            self._priority_weights,
        )
        later = sort_by_priority(
            [item for item in upcoming if item.status != TASK_STATUS_COMPLETED],
            self._priority_weights,
        )

        if overdue:
            self._pack_section("Vencidas", overdue)
        self._pack_section(
            f"Hoy · {format_day_label(today)}",
            open_today + done_today,
            empty_if_missing=True,
        )
        if later:
            self._pack_section("Próximas", later)

    def _fill_completed(self) -> None:
        items = list(self._service.completed())
        if not items:
            empty = Gtk.Label(label="Nada completado todavía", xalign=0)
            empty.get_style_context().add_class("tasks-popup-section-empty")
            self._list.pack_start(empty, False, False, 0)
            return
        self._pack_section("Completadas", items)

    def _fill_by_date(self) -> None:
        today = date.today()
        items = sort_by_priority(
            list(self._service.tasks_for_date(self._browse_date)),
            self._priority_weights,
        )
        heading = f"{format_day_label(self._browse_date)}"
        if self._browse_date == today:
            heading = f"Hoy · {heading}"
        if not items:
            self._list.pack_start(self._section_label(heading), False, False, 0)
            empty = Gtk.Label(label="Sin tareas este día", xalign=0)
            empty.get_style_context().add_class("tasks-popup-section-empty")
            self._list.pack_start(empty, False, False, 0)
            return
        self._pack_section(heading, items)

    def _fill_aidyc(self) -> None:
        provider = self._aidyc
        if provider is None:
            self._list.pack_start(self._empty, False, False, 0)
            self._empty.set_text("Configura AIDYC en Ajustes → Avanzado")
            self._empty.show()
            return

        board = list(provider.board())
        if not board and not provider.connected and provider.last_error:
            self._list.pack_start(self._empty, False, False, 0)
            self._empty.set_text(f"Desconectado: {provider.last_error}")
            self._empty.show()
            return
        if not board:
            self._list.pack_start(self._empty, False, False, 0)
            self._empty.set_text("No hay tareas AIDYC en caché")
            self._empty.show()
            return

        pending = sort_by_priority(
            [item for item in board if item.status != TASK_STATUS_COMPLETED],
            self._priority_weights,
        )
        done = [item for item in board if item.status == TASK_STATUS_COMPLETED]
        if pending:
            self._pack_section("Pendientes", pending, aidyc=True)
        if done:
            self._pack_section("Completadas", done, aidyc=True)

    def _pack_section(
        self,
        heading: str,
        items: list[TaskSnapshot],
        *,
        empty_if_missing: bool = False,
        aidyc: bool = False,
    ) -> None:
        if not items:
            if empty_if_missing:
                empty = Gtk.Label(label="Nada pendiente hoy", xalign=0)
                empty.get_style_context().add_class("tasks-popup-section-empty")
                self._list.pack_start(self._section_label(heading), False, False, 0)
                self._list.pack_start(empty, False, False, 0)
            return
        self._list.pack_start(self._section_label(heading), False, False, 0)
        for snapshot in items:
            meta = None
            snooze = None
            on_delete = None if aidyc else self._service.delete
            if aidyc:
                link = self._aidyc.get_link(snapshot.id) if self._aidyc else None
                if link is not None:
                    parts = [meta_label(snapshot), assignee_label(link.snapshot)]
                    updated = str(link.snapshot.get("fecha_actualizacion") or "").strip()
                    if updated:
                        parts.append(f"act. {updated}")
                    meta = " · ".join(p for p in parts if p)
                snooze = self._make_aidyc_snooze(snapshot)
            row = TaskRow(
                snapshot,
                on_toggle=self._make_toggle(snapshot, aidyc=aidyc),
                on_delete=on_delete,
                on_edit=self._on_edit,
                can_toggle=snapshot.status != TASK_STATUS_MISSED,
                category_label=self._category_labels.get(snapshot.category_id or "")
                or (snapshot.category_id or None),
                priority_label=self._priority_labels.get(snapshot.priority_id or "")
                or (snapshot.priority_id or None),
                meta_override=meta,
                on_snooze=snooze,
            )
            self._list.pack_start(row, False, False, 0)

    def _make_toggle(self, snapshot: TaskSnapshot, *, aidyc: bool = False):
        when = parse_iso_date(snapshot.occurrence_date) or date.today()

        def _toggle(_task_id: str) -> None:
            if aidyc and self._aidyc is not None:
                self._aidyc.toggle(snapshot.id)
                self.refresh()
                return
            self._service.toggle(snapshot.id, on_date=when)

        return _toggle

    def _make_aidyc_snooze(self, snapshot: TaskSnapshot):
        def _snooze(_task_id: str) -> None:
            if self._aidyc is None:
                return
            self._aidyc.posponer(snapshot.id, minutos=15)
            self.refresh()

        return _snooze

    @staticmethod
    def _section_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("tasks-popup-section")
        return label

    def _reload_taxonomy(self) -> None:
        categories = ()
        priorities = ()
        manager = getattr(self._shell_window, "settings_manager", None)
        if manager is not None:
            categories = parse_categories(str(manager.get("widgets.task_categories_json") or ""))
            priorities = parse_priorities(str(manager.get("widgets.task_priorities_json") or ""))
        else:
            categories = parse_categories(None)
            priorities = parse_priorities(None)
        self._category_labels = category_label_map(categories)
        self._priority_labels = priority_label_map(priorities)
        self._priority_weights = priority_weight_map(priorities)
        self._composer.set_taxonomy(categories, priorities)

    def _ensure_aidyc_provider(self) -> None:
        manager = getattr(self._shell_window, "settings_manager", None)
        if manager is None:
            self._aidyc = None
            return
        self._aidyc = build_aidyc_provider_from_settings(manager.get)

    def _update_status_banner(self) -> None:
        if self._active_source != _SOURCE_AIDYC:
            self._status_label.hide()
            return
        self._status_label.show()
        if self._aidyc is None:
            self._status_label.set_text("AIDYC desactivado o sin configurar")
            return
        if self._aidyc.connected:
            sync = self._aidyc.last_sync or "—"
            self._status_label.set_text(f"Conectado · sync {sync}")
        else:
            err = self._aidyc.last_error or "sin conexión"
            self._status_label.set_text(f"Desconectado · {err}")

    def _on_source_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active():
            if self._active_source == key:
                button.handler_block_by_func(self._on_source_toggled)
                button.set_active(True)
                button.handler_unblock_by_func(self._on_source_toggled)
            return
        self._active_source = key
        for other_key, other in self._source_buttons.items():
            if other_key == key:
                continue
            other.handler_block_by_func(self._on_source_toggled)
            other.set_active(False)
            other.handler_unblock_by_func(self._on_source_toggled)
        self._hide_composer()
        if key == _SOURCE_AIDYC:
            self._ensure_aidyc_provider()
            self._pull_aidyc_async()
            self._load_aidyc_users()
            self._assignee_row.show_all()
        else:
            self._assignee_row.hide()
        self.refresh()

    def _on_tab_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active():
            if self._active_tab == key:
                button.handler_block_by_func(self._on_tab_toggled)
                button.set_active(True)
                button.handler_unblock_by_func(self._on_tab_toggled)
            return
        self._active_tab = key
        for other_key, other in self._tab_buttons.items():
            if other_key == key:
                continue
            other.handler_block_by_func(self._on_tab_toggled)
            other.set_active(False)
            other.handler_unblock_by_func(self._on_tab_toggled)
        self.refresh()

    def _on_date_today(self, *_args) -> None:
        self._browse_date = date.today()
        self._date_entry.set_text(self._browse_date.isoformat())
        self.refresh()

    def _on_date_apply(self, *_args) -> None:
        parsed = parse_iso_date(self._date_entry.get_text().strip())
        if parsed is None:
            self._date_entry.set_text(self._browse_date.isoformat())
            return
        self._browse_date = parsed
        self._date_entry.set_text(parsed.isoformat())
        self.refresh()

    def _on_toggle_composer(self, *_args) -> None:
        if self._composer.get_visible():
            self._hide_composer()
            return
        self._reload_taxonomy()
        if self._active_source == _SOURCE_AIDYC:
            self._load_aidyc_users()
            self._assignee_row.show_all()
        self._composer.reveal()
        self.queue_resize()
        self._reposition()

    def _hide_composer(self) -> None:
        self._composer.hide()
        self._composer.set_no_show_all(True)
        if self._active_source != _SOURCE_AIDYC:
            self._assignee_row.hide()
        self.queue_resize()
        self._reposition()

    def _on_composer_submit(self, payload: dict) -> None:
        if self._active_source == _SOURCE_AIDYC:
            self._submit_aidyc(payload)
            self._hide_composer()
            return
        task_id = payload.get("id")
        common = {
            "notes": payload.get("notes", ""),
            "repeat": payload.get("repeat", "none"),
            "due_date": payload.get("due_date"),
            "month_day": payload.get("month_day", 1),
            "category_id": payload.get("category_id"),
            "priority_id": payload.get("priority_id"),
        }
        if task_id:
            self._service.update_task(task_id, payload["title"], **common)
        else:
            self._service.add_task(payload["title"], **common)
        self._hide_composer()

    def _submit_aidyc(self, payload: dict) -> None:
        if self._aidyc is None:
            return
        body = {
            "titulo": payload.get("title", ""),
            "descripcion": payload.get("notes", ""),
            "categoria": payload.get("category_id") or "general",
            "prioridad": payload.get("priority_id") or "media",
            "fecha_limite": payload.get("due_date") or "",
        }
        assignee = self._assignee_combo.get_active_id() or ""
        if assignee:
            body["asignado_a"] = assignee
        task_id = payload.get("id")
        if task_id:
            try:
                self._aidyc.update(str(task_id), body)
            except AidycConflictError:
                self._status_label.set_text("Conflicto de versión: sincroniza y reintenta")
                self._status_label.show()
                self._aidyc.pull()
        else:
            self._aidyc.create(body)
        self.refresh()

    def _on_edit(self, snapshot: TaskSnapshot) -> None:
        if self._active_source == _SOURCE_AIDYC:
            if self._aidyc is None:
                return
            link = self._aidyc.get_link(snapshot.id)
            if link is None:
                return
            self._reload_taxonomy()
            self._load_aidyc_users()
            self._assignee_row.show_all()
            snap = link.snapshot
            assignee = str(snap.get("asignado_a") or "")
            if self._assignee_combo.get_active_id() is not None:
                try:
                    self._assignee_combo.set_active_id(assignee)
                except Exception:
                    self._assignee_combo.set_active_id("")
            self._composer.edit(
                link.local_id,
                title=str(snap.get("titulo") or ""),
                notes=str(snap.get("descripcion") or ""),
                repeat="none",
                due_date=(str(snap.get("fecha_limite") or "")[:10] or None),
                month_day=1,
                category_id=str(snap.get("categoria") or "") or None,
                priority_id=str(snap.get("prioridad") or "") or None,
            )
            self.queue_resize()
            self._reposition()
            return

        record = next((item for item in self._service.records() if item.id == snapshot.id), None)
        if record is None:
            return
        self._reload_taxonomy()
        self._composer.edit(
            record.id,
            title=record.title,
            notes=record.notes,
            repeat=record.repeat,
            due_date=record.due_date,
            month_day=record.month_day,
            category_id=record.category_id,
            priority_id=record.priority_id,
        )
        self.queue_resize()
        self._reposition()

    def _load_aidyc_users(self) -> None:
        self._assignee_combo.remove_all()
        self._assignee_combo.append("", "Sin asignar / yo")
        if self._aidyc is None:
            self._assignee_combo.set_active_id("")
            return
        users = self._aidyc.list_usuarios()
        self._aidyc_users = users
        for user in users:
            usuario = str(user.get("usuario") or "").strip()
            if not usuario:
                continue
            nombre = str(user.get("nombre") or usuario).strip()
            self._assignee_combo.append(usuario, f"{nombre} ({usuario})")
        self._assignee_combo.set_active_id("")

    def _on_sync_clicked(self, *_args) -> None:
        self._pull_aidyc_async()

    def _pull_aidyc_async(self) -> None:
        if self._aidyc is None:
            self._ensure_aidyc_provider()
        provider = self._aidyc
        if provider is None:
            self.refresh()
            return

        def _work() -> bool:
            provider.pull()
            self.refresh()
            return False

        GLib.idle_add(_work)

    def _start_aidyc_poll(self) -> None:
        self._stop_aidyc_poll()
        manager = getattr(self._shell_window, "settings_manager", None)
        try:
            seconds = int(manager.get("aidyc.poll_sec")) if manager else 60
        except Exception:
            seconds = 60
        seconds = max(15, min(seconds, 600))

        def _tick() -> bool:
            if self._active_source == _SOURCE_AIDYC and self.get_visible():
                self._pull_aidyc_async()
            return True

        self._aidyc_poll_id = GLib.timeout_add_seconds(seconds, _tick)

    def _stop_aidyc_poll(self) -> None:
        if self._aidyc_poll_id:
            GLib.source_remove(self._aidyc_poll_id)
            self._aidyc_poll_id = 0

    def _on_size_allocate(self, _widget: Gtk.Widget, allocation: Gtk.Allocation) -> None:
        height = int(allocation.height)
        if height <= 1 or height == self._last_height:
            return
        self._last_height = height
        self._reposition()

    def _reposition(self) -> None:
        if self.get_visible() and self._anchor_button is not None:
            schedule_popup_position(self._position_after_show)

    def _position_after_show(self) -> bool:
        if self._anchor_button is not None:
            top = position_popup_below_anchor(
                self,
                self._anchor_button,
                title=TITLE_TASKS,
                offset=TASKS_POPUP_OFFSET,
                fixed_top=self._fixed_popup_top,
            )
            if self._fixed_popup_top is None and top is not None:
                self._fixed_popup_top = top
        return False
