#!/usr/bin/env python3
"""Workspace visual module; it only renders domain models supplied by the app and emits events."""

from __future__ import annotations

from typing import Callable, Iterable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, GObject, Gtk

from ...config import (
    APPLICATION_ICON_SIZE,
    APPLICATION_ICON_SPACING,
    FOCUSED_APPLICATION_ICON_SIZE,
    WORKSPACE_BUTTON_SPACING,
    WORKSPACE_VISIBLE_ICON_LIMIT,
)
from ...eventbus import EventBus
from ...models import (
    AudioSnapshot,
    HyprlandSnapshot,
    Window,
    Workspace,
    WorkspaceAudioState,
    reorder_workspace_order,
)

WORKSPACE_CHANGED = "workspace_changed"
WORKSPACE_REQUESTED = "workspace_requested"
WINDOW_OPENED = "window_opened"
WINDOW_CLOSED = "window_closed"

WORKSPACE_HOVER_STARTED = "workspace_hover_started"
WORKSPACE_HOVER_ENDED = "workspace_hover_ended"
WORKSPACE_AUDIO_REQUESTED = "workspace_audio_requested"
AUDIO_CHANGED = "audio_changed"


class WorkspaceButton(Gtk.Button):
    """Workspace block: hover for popup, left click switches, right click opens panel."""

    __gsignals__ = {
        "workspace-entered": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "workspace-left": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(
        self,
        workspace_id: int,
        on_activate: Callable[[int], None],
        on_audio_request: Callable[[int, Gtk.Widget], None],
    ) -> None:
        super().__init__()
        self.workspace_id = workspace_id
        self._workspace: Workspace | None = None
        self._on_audio_request = on_audio_request
        self.get_style_context().add_class("workspace-button")
        self.connect("clicked", self._activate, on_activate)
        self.connect("button-press-event", self._on_button_press)

        # Gtk.Button does not reliably receive enter/leave when the pointer moves
        # over child widgets. Track hover on an inner EventBox that wraps all content.
        self._hover_surface = Gtk.EventBox()
        self._hover_surface.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )
        self._hover_surface.connect("enter-notify-event", self._on_pointer_enter)
        self._hover_surface.connect("leave-notify-event", self._on_pointer_leave)
        self._hover_surface.connect("button-press-event", self._on_hover_surface_press)

        self._content = Gtk.Stack()
        self._content.set_transition_type(Gtk.StackTransitionType.NONE)
        self._content.set_homogeneous(False)
        self._dot = self._make_dot()
        self._icons = Gtk.Box(spacing=APPLICATION_ICON_SPACING)
        self._icons.get_style_context().add_class("application-icons")
        self._content.add_named(self._dot, "dot")
        self._content.add_named(self._icons, "icons")
        self._hover_surface.add(self._content)
        self.add(self._hover_surface)
        self._content.show_all()

    def _activate(self, _button: Gtk.Button, on_activate: Callable[[int], None]) -> None:
        on_activate(self.workspace_id)

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            self._on_audio_request(self.workspace_id, self)
            return True
        return False

    def _on_hover_surface_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            self._on_audio_request(self.workspace_id, self)
            return True
        return False

    def _on_pointer_enter(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self.emit("workspace-entered", self.workspace_id)
        return False

    def _on_pointer_leave(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self.emit("workspace-left", self.workspace_id)
        return False

    def update(self, workspace: Workspace) -> None:
        """Apply only the visual differences for this workspace."""
        previous = self._workspace
        if previous == workspace:
            return

        context = self.get_style_context()
        if workspace.is_special:
            context.add_class("special")
        else:
            context.remove_class("special")

        if workspace.active:
            context.add_class("active")
        else:
            context.remove_class("active")

        if workspace.icons:
            context.remove_class("empty-active")
            if (
                previous is None
                or previous.icons != workspace.icons
                or previous.focused_window_address != workspace.focused_window_address
                or previous.windows != workspace.windows
            ):
                self._set_icons(workspace)
            self._content.set_visible_child_name("icons")
        else:
            if workspace.active:
                context.add_class("empty-active")
            else:
                context.remove_class("empty-active")
            self._content.set_visible_child_name("dot")

        self._workspace = workspace

    def update_audio_state(self, audio_state: WorkspaceAudioState | None) -> None:
        """Apply CSS classes reflecting audio status of this workspace."""
        context = self.get_style_context()
        has_audio = bool(audio_state and audio_state.has_audio)
        is_playing = bool(audio_state and audio_state.is_playing)
        is_muted = bool(audio_state and audio_state.has_muted)

        if has_audio:
            context.add_class("audio-has-stream")
        else:
            context.remove_class("audio-has-stream")

        if is_playing:
            context.add_class("audio-playing")
        else:
            context.remove_class("audio-playing")

        if is_muted:
            context.add_class("audio-muted")
        else:
            context.remove_class("audio-muted")

    @property
    def workspace(self) -> Workspace | None:
        return self._workspace

    @staticmethod
    def _make_dot() -> Gtk.Label:
        dot = Gtk.Label(label="●")
        dot.get_style_context().add_class("workspace-dot")
        return dot

    def _set_icons(self, workspace: Workspace) -> None:
        for child in self._icons.get_children():
            self._icons.remove(child)
        visible_pairs = tuple(zip(workspace.windows, workspace.icons))[:WORKSPACE_VISIBLE_ICON_LIMIT]
        for window, icon in visible_pairs:
            application_icon = WorkspaceApplicationIcon(
                window,
                icon,
                window.address == workspace.focused_window_address,
            )
            self._icons.pack_start(application_icon, False, False, 0)
        hidden_count = len(workspace.windows) - len(visible_pairs)
        if hidden_count:
            overflow = Gtk.Label(label=f"+{hidden_count}")
            overflow.get_style_context().add_class("workspace-overflow")
            self._icons.pack_start(overflow, False, False, 0)
        self._icons.show_all()


class WorkspaceApplicationIcon(Gtk.EventBox):
    """Per-application icon inside a workspace block."""

    __gsignals__ = {
        "application-entered": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "application-left": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, window: Window, icon_name: str, focused: bool) -> None:
        super().__init__()
        self._window_address = window.address
        context = self.get_style_context()
        context.add_class("workspace-application-icon")
        if focused:
            context.add_class("focused")

        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        image.set_pixel_size(FOCUSED_APPLICATION_ICON_SIZE if focused else APPLICATION_ICON_SIZE)
        self.add(image)
        self.connect("enter-notify-event", self._on_pointer_enter)
        self.connect("leave-notify-event", self._on_pointer_leave)

    def _on_pointer_enter(self, _icon: Gtk.EventBox, _event: object) -> bool:
        self.emit("application-entered", self._window_address)
        return False

    def _on_pointer_leave(self, _icon: Gtk.EventBox, _event: object) -> bool:
        self.emit("application-left", self._window_address)
        return False


class WorkspaceWidget(Gtk.Box):
    """Renders the workspace strip and publishes pointer/click events to EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(spacing=WORKSPACE_BUTTON_SPACING)
        self._event_bus = event_bus
        self.buttons: dict[int, WorkspaceButton] = {}
        self._audio_snapshot: AudioSnapshot | None = None
        self._current_workspaces: tuple[Workspace, ...] = ()
        self._manual_order: tuple[int, ...] = ()
        self._drag_targets = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]
        self.get_style_context().add_class("workspace-strip")

        self._event_bus.subscribe(WORKSPACE_CHANGED, self._on_workspace_changed)
        self._event_bus.subscribe(WINDOW_OPENED, self._on_workspace_changed)
        self._event_bus.subscribe(WINDOW_CLOSED, self._on_workspace_changed)
        self._event_bus.subscribe(AUDIO_CHANGED, self._on_audio_changed)
        self.connect("destroy", self._on_destroy)

    def render(self, workspaces: Iterable[Workspace]) -> None:
        workspaces = self._apply_manual_order(tuple(workspaces))
        self._current_workspaces = workspaces
        self._manual_order = tuple(workspace.id for workspace in workspaces)
        for position, workspace in enumerate(workspaces):
            button = self._button_for(workspace.id)
            button.update(workspace)
            self.reorder_child(button, position)

        visible_ids = {workspace.id for workspace in workspaces}
        for workspace_id, button in tuple(self.buttons.items()):
            if workspace_id not in visible_ids:
                self.remove(button)
                del self.buttons[workspace_id]

        self.show_all()
        if self._audio_snapshot is not None:
            GLib.idle_add(self._apply_audio_snapshot, self._audio_snapshot)

    def _apply_manual_order(self, workspaces: tuple[Workspace, ...]) -> tuple[Workspace, ...]:
        ids = {workspace.id for workspace in workspaces}
        if not self._manual_order:
            return workspaces
        ordered_ids = [workspace_id for workspace_id in self._manual_order if workspace_id in ids]
        for workspace in workspaces:
            if workspace.id not in ordered_ids:
                ordered_ids.append(workspace.id)
        lookup = {workspace.id: workspace for workspace in workspaces}
        return tuple(lookup[workspace_id] for workspace_id in ordered_ids)

    def _reorder_workspace_block(self, source_id: int, target_id: int) -> None:
        if source_id == target_id:
            return
        self._manual_order = tuple(
            workspace.id
            for workspace in reorder_workspace_order(
                tuple(self._current_workspaces),
                source_id,
                target_id,
            )
        )
        self.render(self._current_workspaces)

    def _on_drag_data_get(self, widget: Gtk.Widget, _context: Gdk.DragContext, selection_data: Gtk.SelectionData, _info: int, _time: int, workspace_id: int) -> None:
        selection_data.set_text(str(workspace_id), -1)

    def _on_drag_data_received(self, widget: Gtk.Widget, _context: Gdk.DragContext, _x: int, _y: int, selection_data: Gtk.SelectionData, _info: int, _time: int, target_id: int) -> None:
        text = selection_data.get_text()
        if text is None:
            return
        try:
            source_id = int(text)
        except ValueError:
            return
        self._reorder_workspace_block(source_id, target_id)

    def get_button(self, workspace_id: int) -> WorkspaceButton | None:
        return self.buttons.get(workspace_id)

    def _button_for(self, workspace_id: int) -> WorkspaceButton:
        button = self.buttons.get(workspace_id)
        if button is None:
            button = WorkspaceButton(
                workspace_id,
                self._on_button_clicked,
                self._on_audio_requested,
            )
            button.connect("workspace-entered", self._on_workspace_entered)
            button.connect("workspace-left", self._on_workspace_left)
            button.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self._drag_targets, Gdk.DragAction.MOVE)
            button.drag_source_add_text_targets()
            button.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT, self._drag_targets, Gdk.DragAction.MOVE)
            button.drag_dest_add_text_targets()
            button.connect("drag-data-get", self._on_drag_data_get, workspace_id)
            button.connect("drag-data-received", self._on_drag_data_received, workspace_id)
            self.buttons[workspace_id] = button
            self.pack_start(button, False, False, 0)
        return button

    def _on_button_clicked(self, workspace_id: int) -> None:
        self._event_bus.emit(WORKSPACE_REQUESTED, workspace_id)

    def _on_audio_requested(self, workspace_id: int, widget: Gtk.Widget) -> None:
        self._event_bus.emit(
            WORKSPACE_AUDIO_REQUESTED,
            {"workspace_id": workspace_id, "widget": widget},
        )

    def _on_workspace_entered(self, button: WorkspaceButton, workspace_id: int) -> None:
        self._event_bus.emit(
            WORKSPACE_HOVER_STARTED,
            {"workspace_id": workspace_id, "widget": button},
        )

    def _on_workspace_left(self, _button: WorkspaceButton, workspace_id: int) -> None:
        self._event_bus.emit(
            WORKSPACE_HOVER_ENDED,
            {"workspace_id": workspace_id},
        )

    def _on_workspace_changed(self, snapshot: HyprlandSnapshot) -> None:
        GLib.idle_add(self.render, snapshot.workspaces)

    def _on_audio_changed(self, snapshot: AudioSnapshot) -> None:
        self._audio_snapshot = snapshot
        GLib.idle_add(self._apply_audio_snapshot, snapshot)

    def _apply_audio_snapshot(self, snapshot: AudioSnapshot) -> bool:
        for ws_id, button in self.buttons.items():
            audio_state = snapshot.get_workspace_audio(ws_id)
            button.update_audio_state(audio_state)
        return False

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(WORKSPACE_CHANGED, self._on_workspace_changed)
        self._event_bus.unsubscribe(WINDOW_OPENED, self._on_workspace_changed)
        self._event_bus.unsubscribe(WINDOW_CLOSED, self._on_workspace_changed)
        self._event_bus.unsubscribe(AUDIO_CHANGED, self._on_audio_changed)
