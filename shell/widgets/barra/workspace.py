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
_WORKSPACE_DRAG_THRESHOLD_PX = 8


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
        self._dragged_workspace_id: int | None = None
        self._drag_target_workspace_id: int | None = None
        self._drag_press_root: tuple[float, float] | None = None
        self._workspace_dragging = False
        self._suppress_workspace_click = False
        self.get_style_context().add_class("workspace-strip")

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
        self.block_order = tuple(block.block_index for block in blocks)
        self._current_workspaces = tuple(
            workspace
            for block_index in self.block_order
            for workspace in block_map[block_index].workspaces
        )
        for workspace in self._current_workspaces:
            button = self._button_for(workspace.id)
            self._configure_workspace_drag(button, workspace.id)
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
            self.block_widgets[block_index] = block_widget
            self.pack_start(block_widget, False, False, 0)
        return block_widget

    def _configure_workspace_drag(self, button: WorkspaceButton, workspace_id: int) -> None:
        if getattr(button, "_block_drag_configured", False):
            return
        button._block_drag_configured = True
        drag_mask = (
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        for widget in (button, button._hover_surface):
            widget.add_events(drag_mask)
            widget.connect("button-press-event", self._on_workspace_drag_press, workspace_id)
            widget.connect("motion-notify-event", self._on_workspace_drag_motion, workspace_id)
            widget.connect("button-release-event", self._on_workspace_drag_release, workspace_id)

    def _on_workspace_drag_press(
        self,
        _widget: Gtk.Widget,
        event: Gdk.EventButton,
        workspace_id: int,
    ) -> bool:
        if event.button != 1 or workspace_id < 1:
            return False
        self._dragged_workspace_id = workspace_id
        self._drag_target_workspace_id = None
        self._drag_press_root = (float(event.x_root), float(event.y_root))
        self._workspace_dragging = False
        return False

    def _on_workspace_drag_motion(
        self,
        _widget: Gtk.Widget,
        event: Gdk.EventMotion,
        _workspace_id: int,
    ) -> bool:
        if self._dragged_workspace_id is None or self._drag_press_root is None:
            return False
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        if not self._workspace_dragging:
            delta_x = float(event.x_root) - self._drag_press_root[0]
            delta_y = float(event.y_root) - self._drag_press_root[1]
            if (delta_x * delta_x) + (delta_y * delta_y) < _WORKSPACE_DRAG_THRESHOLD_PX ** 2:
                return False
            self._workspace_dragging = True
            self._suppress_workspace_click = True
            source_button = self.buttons.get(self._dragged_workspace_id)
            if source_button is not None:
                source_button.get_style_context().add_class("dragging")
        target_id = self._workspace_id_at_widget_point(_widget, event.x, event.y)
        self._drag_target_workspace_id = target_id
        self._update_drop_target_style(target_id)
        return True

    def _on_workspace_drag_release(
        self,
        _widget: Gtk.Widget,
        event: Gdk.EventButton,
        _workspace_id: int,
    ) -> bool:
        if event.button != 1:
            return False
        source_id = self._dragged_workspace_id
        was_dragging = self._workspace_dragging
        target_id = self._workspace_id_at_widget_point(_widget, event.x, event.y)
        if target_id is None:
            target_id = self._drag_target_workspace_id
        self._reset_workspace_drag()
        if (
            was_dragging
            and source_id is not None
            and target_id is not None
            and source_id != target_id
            and source_id > 0
            and target_id > 0
        ):
            self._event_bus.emit(
                WORKSPACE_REORDER_REQUESTED,
                {
                    "source_workspace": source_id,
                    "target_workspace": target_id,
                },
            )
            GLib.timeout_add(100, self._clear_suppress_workspace_click)
            return True
        if was_dragging:
            GLib.timeout_add(100, self._clear_suppress_workspace_click)
            return True
        self._suppress_workspace_click = False
        return False

    def _update_drop_target_style(self, target_id: int | None) -> None:
        source_id = self._dragged_workspace_id
        for workspace_id, button in self.buttons.items():
            style = button.get_style_context()
            if workspace_id == target_id and workspace_id != source_id:
                style.add_class("drop-target")
            else:
                style.remove_class("drop-target")

    def _workspace_id_at_widget_point(self, widget: Gtk.Widget, x: float, y: float) -> int | None:
        translated = widget.translate_coordinates(self, int(x), int(y))
        if translated is None:
            return None
        pointer_x, pointer_y = translated
        for workspace_id, button in self.buttons.items():
            if workspace_id < 1 or not button.get_mapped():
                continue
            origin = button.translate_coordinates(self, 0, 0)
            if origin is None:
                continue
            button_x, button_y = origin
            allocation = button.get_allocation()
            if (
                button_x <= pointer_x < button_x + allocation.width
                and button_y <= pointer_y < button_y + allocation.height
            ):
                return workspace_id
        return None

    def _reset_workspace_drag(self) -> None:
        self._clear_drag_styles()
        self._dragged_workspace_id = None
        self._drag_target_workspace_id = None
        self._drag_press_root = None
        self._workspace_dragging = False

    def _clear_suppress_workspace_click(self) -> bool:
        self._suppress_workspace_click = False
        return False

    def _clear_drag_styles(self) -> None:
        for button in self.buttons.values():
            button.get_style_context().remove_class("dragging")
            button.get_style_context().remove_class("drop-target")

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
        if self._suppress_workspace_click:
            return
        self._event_bus.emit(WORKSPACE_REQUESTED, workspace_id)

    def _on_audio_requested(self, workspace_id: int, widget: Gtk.Widget) -> None:
        self._event_bus.emit(
            WORKSPACE_AUDIO_REQUESTED,
            {"workspace_id": workspace_id, "widget": widget},
        )

    def _on_workspace_entered(self, button: WorkspaceButton, workspace_id: int) -> None:
        if self._workspace_dragging:
            return
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
        self._reset_workspace_drag()
        self._event_bus.unsubscribe(WORKSPACE_CHANGED, self._on_workspace_changed)
        self._event_bus.unsubscribe(WINDOW_OPENED, self._on_workspace_changed)
        self._event_bus.unsubscribe(WINDOW_CLOSED, self._on_workspace_changed)
        self._event_bus.unsubscribe(AUDIO_CHANGED, self._on_audio_changed)
