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
    WORKSPACES_PER_BLOCK,
)
from ...eventbus import EventBus
from ...models import (
    AudioSnapshot,
    HyprlandSnapshot,
    Window,
    Workspace,
    WorkspaceBlock,
    WorkspaceAudioState,
    compose_workspace_blocks,
)

WORKSPACE_CHANGED = "workspace_changed"
WORKSPACE_REQUESTED = "workspace_requested"
WORKSPACE_REORDER_REQUESTED = "workspace_reorder_requested"
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
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
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


class WorkspaceBlockWidget(Gtk.EventBox):
    """Draggable visual container for a complete group of workspace buttons."""

    def __init__(self, block_index: int) -> None:
        super().__init__()
        self.block_index = block_index
        self.get_style_context().add_class("workspace-block")
        self.content = Gtk.Box(spacing=WORKSPACE_BUTTON_SPACING)
        self.content.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.add(self.content)

    def render(self, block: WorkspaceBlock, buttons: dict[int, WorkspaceButton]) -> None:
        for workspace in block.workspaces:
            button = buttons[workspace.id]
            if button.get_parent() is not self.content:
                self.content.pack_start(button, False, False, 0)
            button.update(workspace)
        self.show_all()


class WorkspaceWidget(Gtk.Box):
    """Renders the workspace strip and publishes pointer/click events to EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(spacing=WORKSPACE_BUTTON_SPACING)
        self._event_bus = event_bus
        self.buttons: dict[int, WorkspaceButton] = {}
        self.block_widgets: dict[int, WorkspaceBlockWidget] = {}
        self._audio_snapshot: AudioSnapshot | None = None
        self._all_workspaces: tuple[Workspace, ...] = ()
        self._current_workspaces: tuple[Workspace, ...] = ()
        self.block_order: tuple[int, ...] = ()
        self._dragged_block_index: int | None = None
        self._drag_target_block_index: int | None = None
        self._drag_ghost: Gtk.Button | None = None
        self._drag_origin_order: tuple[int, ...] = ()
        self._drag_preview_order: tuple[int, ...] = ()
        self._drag_targets = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]
        self.get_style_context().add_class("workspace-strip")
        self.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._drag_targets,
            Gdk.DragAction.MOVE,
        )
        self.connect("drag-motion", self._on_drag_motion)
        self.connect("drag-leave", self._on_drag_leave)
        self.connect("drag-data-received", self._on_drag_data_received)

        self._event_bus.subscribe(WORKSPACE_CHANGED, self._on_workspace_changed)
        self._event_bus.subscribe(WINDOW_OPENED, self._on_workspace_changed)
        self._event_bus.subscribe(WINDOW_CLOSED, self._on_workspace_changed)
        self._event_bus.subscribe(AUDIO_CHANGED, self._on_audio_changed)
        self.connect("destroy", self._on_destroy)

    def render(self, workspaces: Iterable[Workspace]) -> None:
        workspaces = tuple(workspaces)
        self._all_workspaces = workspaces
        blocks = compose_workspace_blocks(workspaces, WORKSPACES_PER_BLOCK)
        block_map = {block.block_index: block for block in blocks}
        block_ids = tuple(block.block_index for block in blocks)
        if not self.block_order:
            self.block_order = block_ids
        else:
            self.block_order = tuple(index for index in self.block_order if index in block_map) + tuple(
                index for index in block_ids if index not in self.block_order
            )
        self._current_workspaces = tuple(
            workspace
            for block_index in self.block_order
            for workspace in block_map[block_index].workspaces
        )
        for workspace in self._current_workspaces:
            button = self._button_for(workspace.id)
            self._configure_workspace_drag(button, block_map[(workspace.id - 1) // WORKSPACES_PER_BLOCK].block_index)
        for position, block_index in enumerate(self.block_order):
            block_widget = self._block_for(block_index)
            block_widget.render(block_map[block_index], self.buttons)
            self.reorder_child(block_widget, position)

        special_workspaces = tuple(workspace for workspace in workspaces if workspace.is_special)
        normal_count = len(self.block_order)
        for offset, workspace in enumerate(special_workspaces):
            button = self._button_for(workspace.id)
            button.update(workspace)
            if button.get_parent() is not self:
                self.pack_start(button, False, False, 0)
            self.reorder_child(button, normal_count + offset)

        for block_index, block_widget in tuple(self.block_widgets.items()):
            if block_index not in block_map:
                self.remove(block_widget)
                del self.block_widgets[block_index]
        visible_special_ids = {workspace.id for workspace in special_workspaces}
        for workspace_id, button in tuple(self.buttons.items()):
            if workspace_id < 0 and workspace_id not in visible_special_ids:
                self.remove(button)
                del self.buttons[workspace_id]

        self.show_all()
        if self._audio_snapshot is not None:
            GLib.idle_add(self._apply_audio_snapshot, self._audio_snapshot)

    def _block_for(self, block_index: int) -> WorkspaceBlockWidget:
        block_widget = self.block_widgets.get(block_index)
        if block_widget is None:
            block_widget = WorkspaceBlockWidget(block_index)
            block_widget.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self._drag_targets, Gdk.DragAction.MOVE)
            block_widget.connect("drag-begin", self._on_drag_begin, block_index)
            block_widget.connect("drag-data-get", self._on_drag_data_get, block_index)
            block_widget.connect("drag-end", self._on_drag_end, block_index)
            block_widget.drag_dest_set(
                Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                self._drag_targets,
                Gdk.DragAction.MOVE,
            )
            block_widget.connect("drag-motion", self._on_block_drag_motion, block_index)
            block_widget.connect("drag-leave", self._on_drag_leave)
            block_widget.connect("drag-data-received", self._on_workspace_drag_data_received, block_index)
            block_widget.content.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self._drag_targets, Gdk.DragAction.MOVE)
            block_widget.content.connect("drag-begin", self._on_drag_begin, block_index)
            block_widget.content.connect("drag-data-get", self._on_drag_data_get, block_index)
            block_widget.content.connect("drag-end", self._on_drag_end, block_index)
            self.block_widgets[block_index] = block_widget
            self.pack_start(block_widget, False, False, 0)
        return block_widget

    def _configure_workspace_drag(self, button: WorkspaceButton, block_index: int) -> None:
        if getattr(button, "_block_drag_configured", False):
            return
        button._block_drag_configured = True
        for widget in (button, button._hover_surface):
            widget.drag_source_set(
                Gdk.ModifierType.BUTTON1_MASK,
                self._drag_targets,
                Gdk.DragAction.MOVE,
            )
            widget.connect("drag-begin", self._on_drag_begin, block_index)
            widget.connect("drag-data-get", self._on_drag_data_get, block_index)
            widget.connect("drag-end", self._on_drag_end, block_index)
            widget.drag_dest_set(
                Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                self._drag_targets,
                Gdk.DragAction.MOVE,
            )
            widget.connect("drag-motion", self._on_block_drag_motion, block_index)
            widget.connect("drag-leave", self._on_drag_leave)
            widget.connect("drag-data-received", self._on_workspace_drag_data_received, block_index)

    def _on_block_drag_motion(self, widget: Gtk.Widget, context: Gdk.DragContext, x: int, y: int, time: int, block_index: int) -> bool:
        allocation = widget.get_allocation()
        return self._on_drag_motion(widget, context, allocation.x + x, y, time, block_index)

    def _on_drag_begin(self, block_widget: WorkspaceBlockWidget, context: Gdk.DragContext, block_index: int) -> None:
        self._dragged_block_index = block_index
        self._drag_target_block_index = None
        self._drag_origin_order = self.block_order
        self._drag_preview_order = self.block_order
        block = self.block_widgets.get(block_index)
        if block is None:
            return
        block.get_style_context().add_class("dragging")
        label = f"WS {block_index * WORKSPACES_PER_BLOCK + 1}-{(block_index + 1) * WORKSPACES_PER_BLOCK}"
        self._drag_ghost = Gtk.Button(label=label)
        self._drag_ghost.get_style_context().add_class("workspace-button")
        self._drag_ghost.get_style_context().add_class("drag-ghost")
        allocation = block.get_allocation()
        self._drag_ghost.set_size_request(allocation.width, allocation.height)
        self._drag_ghost.show()
        Gtk.drag_set_icon_widget(context, self._drag_ghost, 0, 0)

    def _on_drag_data_get(self, _block_widget: WorkspaceBlockWidget, _context: Gdk.DragContext, selection_data: Gtk.SelectionData, _info: int, _time: int, block_index: int) -> None:
        selection_data.set_text(str(block_index), -1)

    def _on_drag_end(self, _block_widget: WorkspaceBlockWidget, _context: Gdk.DragContext, _block_index: int) -> None:
        for block_widget in self.block_widgets.values():
            block_widget.get_style_context().remove_class("dragging")
            block_widget.get_style_context().remove_class("drop-target")
        if self._drag_ghost is not None:
            self._drag_ghost.destroy()
            self._drag_ghost = None
        source_index = self._dragged_block_index
        target_index = self._drag_target_block_index
        if source_index is not None and target_index is not None and source_index != target_index:
            source_position = self.block_order.index(source_index)
            target_position = self.block_order.index(target_index)
            block_order = list(self.block_order)
            block_order[source_position], block_order[target_position] = (
                block_order[target_position],
                block_order[source_position],
            )
            self.block_order = tuple(block_order)
            self.render(self._all_workspaces)
            self._event_bus.emit(
                WORKSPACE_REORDER_REQUESTED,
                {
                    "block_order": self.block_order,
                    "source_block": source_index,
                    "target_block": target_index,
                },
            )
        self._dragged_block_index = None
        self._drag_target_block_index = None
        self._drag_origin_order = ()
        self._drag_preview_order = ()

    def _on_drag_motion(self, _widget: Gtk.Widget, context: Gdk.DragContext, x: int, _y: int, time: int, target_block_index: int | None = None) -> bool:
        source_index = self._dragged_block_index
        if source_index is None:
            return False
        visible_indices = [index for index in self._drag_origin_order if index != source_index]
        insertion_index = len(visible_indices)
        target_index = target_block_index if target_block_index is not None else source_index
        for index, block_index in enumerate(visible_indices):
            if target_block_index is None:
                block_widget = self.block_widgets[block_index]
                allocation = block_widget.get_allocation()
                if x < allocation.x + allocation.width / 2:
                    insertion_index = index
                    target_index = block_index
                    break
            elif block_index == target_block_index:
                insertion_index = index
                break
        if target_block_index is not None and target_block_index in visible_indices:
            insertion_index = visible_indices.index(target_block_index)
        self._drag_target_block_index = target_index
        for block_widget in self.block_widgets.values():
            block_widget.get_style_context().remove_class("drop-target")
        if target_index != source_index:
            self.block_widgets[target_index].get_style_context().add_class("drop-target")
        Gdk.drag_status(context, Gdk.DragAction.MOVE, time)
        return True

    def _on_drag_leave(self, _widget: Gtk.Widget, _context: Gdk.DragContext, _time: int) -> None:
        for block_widget in self.block_widgets.values():
            block_widget.get_style_context().remove_class("drop-target")

    def _on_workspace_drag_data_received(self, _widget: Gtk.Widget, context: Gdk.DragContext, _x: int, _y: int, _selection_data: Gtk.SelectionData, _info: int, time: int, block_index: int) -> None:
        self._drag_target_block_index = block_index
        Gtk.drag_finish(context, True, False, time)

    def _on_drag_data_received(self, _widget: Gtk.Widget, context: Gdk.DragContext, _x: int, _y: int, _selection_data: Gtk.SelectionData, _info: int, time: int) -> None:
        Gtk.drag_finish(context, True, False, time)

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
            self.buttons[workspace_id] = button
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
