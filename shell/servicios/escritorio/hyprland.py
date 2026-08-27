"""Single source of truth for Hyprland IPC state and high-level shell events."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from typing import Any

from ...eventbus import EventBus
from ...icons import DESKTOP_ICON, application_for_window, icon_for_window
from ...models import (
    ActiveWindow,
    HyprlandSnapshot,
    Window,
    WorkspaceRecord,
    compose_workspaces,
    with_active_workspace,
    with_focused_window,
)


WORKSPACE_CHANGED = "workspace_changed"
WORKSPACE_REQUESTED = "workspace_requested"
ACTIVE_WINDOW_CHANGED = "active_window_changed"
WINDOW_OPENED = "window_opened"
WINDOW_CLOSED = "window_closed"
FULLSCREEN_CHANGED = "fullscreen_changed"
MONITOR_CHANGED = "monitor_changed"


class HyprlandError(RuntimeError):
    """Raised when the compositor IPC cannot provide a valid answer."""


class HyprlandService:
    """Owns one socket listener, the current snapshot, and all Hyprland commands."""

    def __init__(self, event_bus: EventBus, persistent_workspaces: int | None) -> None:
        self._event_bus = event_bus
        self._persistent_workspaces = persistent_workspaces
        self._snapshot: HyprlandSnapshot | None = None
        self._snapshot_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._socket: socket.socket | None = None
        self._socket_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._command_threads: set[threading.Thread] = set()
        self._command_threads_lock = threading.Lock()
        self._event_bus.subscribe(
            WORKSPACE_REQUESTED,
            self._on_workspace_requested,
            on_main=False,
        )

    @property
    def snapshot(self) -> HyprlandSnapshot | None:
        with self._snapshot_lock:
            return self._snapshot

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._event_loop,
            name="hyprland-socket2-listener",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        self._event_bus.unsubscribe(WORKSPACE_REQUESTED, self._on_workspace_requested)
        with self._socket_lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._socket.close()
                self._socket = None
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        with self._command_threads_lock:
            command_threads = tuple(self._command_threads)
        for command_thread in command_threads:
            if command_thread is not threading.current_thread():
                command_thread.join(timeout=2.0)

    def _on_workspace_requested(self, workspace_id: Any) -> None:
        """Run compositor commands off GTK's main thread."""
        if self._stop_event.is_set():
            return

        def worker() -> None:
            try:
                self._handle_workspace_requested(workspace_id)
            finally:
                with self._command_threads_lock:
                    self._command_threads.discard(threading.current_thread())

        command_thread = threading.Thread(
            target=worker,
            name="hyprland-command",
            daemon=True,
        )
        with self._command_threads_lock:
            self._command_threads.add(command_thread)
        command_thread.start()

    def _handle_workspace_requested(self, workspace_id: Any) -> None:
        try:
            requested_id = int(workspace_id)
        except (TypeError, ValueError) as error:
            print(f"shell: {error}")
            return

        snapshot = self.snapshot
        workspace_name: str | None = None
        is_special = requested_id < 0
        if snapshot is not None:
            for workspace in snapshot.workspaces:
                if workspace.id == requested_id:
                    workspace_name = workspace.name
                    is_special = workspace.is_special
                    if workspace.active and not is_special:
                        if not self._get_visible_special_workspace_names():
                            return
                    break

        if is_special and workspace_name and self._special_workspace_is_visible(workspace_name):
            return

        try:
            self._dispatch_workspace(requested_id, workspace_name, is_special)
        except HyprlandError as error:
            print(f"shell: {error}")

    def _event_loop(self) -> None:
        """Reconnect after restarts while retaining exactly one active socket reader."""
        while not self._stop_event.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as event_socket:
                    event_socket.settimeout(1.0)
                    event_socket.connect(str(self._event_socket_path()))
                    with self._socket_lock:
                        self._socket = event_socket
                    self._read_events(event_socket)
            except (HyprlandError, FileNotFoundError, ConnectionRefusedError, OSError):
                self._stop_event.wait(1)
            finally:
                with self._socket_lock:
                    self._socket = None

    def _read_events(self, event_socket: socket.socket) -> None:
        pending = b""
        while not self._stop_event.is_set():
            try:
                chunk = event_socket.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                return
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            for raw_line in lines:
                event, separator, _payload = raw_line.decode("utf-8", errors="replace").partition(">>")
                if separator:
                    self._handle_socket_event(event)

    def _handle_socket_event(self, event: str) -> None:
        try:
            if event in {"workspace", "workspacev2", "focusedmon"}:
                snapshot = self._refresh_active_workspace()
                self._emit(WORKSPACE_CHANGED, snapshot)
                self._emit_active_window(snapshot)
            elif event == "activewindow":
                snapshot = self._refresh_active_window()
                self._emit_active_window(snapshot)
                self._emit(WORKSPACE_CHANGED, snapshot)
            elif event == "openwindow":
                snapshot = self._refresh_full()
                self._emit(WINDOW_OPENED, snapshot)
                self._emit_active_window(snapshot)
            elif event == "closewindow":
                snapshot = self._refresh_full()
                self._emit(WINDOW_CLOSED, snapshot)
                self._emit_active_window(snapshot)
            elif event in {"movewindow", "movewindowv2", "createworkspace", "destroyworkspace", "renameworkspace", "urgent"}:
                snapshot = self._refresh_full()
                self._emit(WORKSPACE_CHANGED, snapshot)
                self._emit_active_window(snapshot)
            elif event == "fullscreen":
                snapshot = self._refresh_full()
                self._emit(FULLSCREEN_CHANGED, snapshot)
                self._emit_active_window(snapshot)
            elif event in {"monitoradded", "monitoraddedv2", "monitorremoved", "monitorremovedv2"}:
                snapshot = self._refresh_full()
                self._emit(MONITOR_CHANGED, snapshot)
                self._emit_active_window(snapshot)
        except HyprlandError as error:
            print(f"shell: {error}")

    def _refresh_full(self) -> HyprlandSnapshot:
        records = tuple(self._workspace(item) for item in self._json("workspaces"))
        windows = tuple(self._window(item) for item in self._json("clients"))
        active_workspace_id = int(self._json("activeworkspace").get("id", 0))
        active_window = self._active_window(self._json("activewindow"))
        snapshot = HyprlandSnapshot(
            workspaces=compose_workspaces(
                records,
                windows,
                active_workspace_id,
                self._persistent_workspaces,
                icon_for_window,
                active_window.address or None,
            ),
            active_window=active_window,
        )
        return self._replace_snapshot(snapshot)

    def _refresh_active_workspace(self) -> HyprlandSnapshot:
        active_workspace_id = int(self._json("activeworkspace").get("id", 0))
        active_window = self._active_window(self._json("activewindow"))
        snapshot = self.snapshot
        if snapshot is None or not any(workspace.id == active_workspace_id for workspace in snapshot.workspaces):
            return self._refresh_full()
        return self._replace_snapshot(
            replace(
                snapshot,
                active_window=active_window,
                workspaces=with_focused_window(
                    with_active_workspace(snapshot.workspaces, active_workspace_id),
                    active_window.address or None,
                ),
            )
        )

    def _refresh_active_window(self) -> HyprlandSnapshot:
        snapshot = self.snapshot
        if snapshot is None:
            return self._refresh_full()
        active_window = self._active_window(self._json("activewindow"))
        return self._replace_snapshot(
            replace(
                snapshot,
                active_window=active_window,
                workspaces=with_focused_window(snapshot.workspaces, active_window.address or None),
            )
        )

    def _replace_snapshot(self, snapshot: HyprlandSnapshot) -> HyprlandSnapshot:
        with self._snapshot_lock:
            self._snapshot = snapshot
        return snapshot

    def _emit(self, event_name: str, snapshot: HyprlandSnapshot) -> None:
        self._event_bus.emit(event_name, snapshot)

    def _emit_active_window(self, snapshot: HyprlandSnapshot) -> None:
        self._event_bus.emit(ACTIVE_WINDOW_CHANGED, snapshot.active_window)

    @staticmethod
    def _workspace(item: dict[str, Any]) -> WorkspaceRecord:
        workspace_id = int(item["id"])
        return WorkspaceRecord(
            id=workspace_id,
            name=str(item.get("name", item["id"])),
            is_special=workspace_id < 0,
        )

    @staticmethod
    def _window(item: dict[str, Any]) -> Window:
        workspace = item.get("workspace") or {}
        app_class = str(item.get("class", ""))
        pid = item.get("pid")
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None

        window = Window(
            address=str(item.get("address", "")),
            app_class=app_class,
            title=str(item.get("title", "")),
            workspace_id=int(workspace.get("id", 0)),
            pid=pid,
        )
        application = application_for_window(window)
        return Window(
            address=window.address,
            app_class=app_class,
            title=window.title,
            workspace_id=window.workspace_id,
            application_name=application.name,
            icon=application.icon,
            pid=window.pid,
        )

    @staticmethod
    def _active_window(item: dict[str, Any]) -> ActiveWindow:
        address = str(item.get("address", ""))
        if not address:
            return ActiveWindow(
                address="",
                app_class="",
                application_name="Escritorio",
                title="Sin ventanas activas",
                icon=DESKTOP_ICON,
            )
        window = Window(
            address=address,
            app_class=str(item.get("class", "")),
            title=str(item.get("title", "")),
            workspace_id=0,
        )
        application = application_for_window(window)
        fullscreen = int(item.get("fullscreen", 0) or 0)
        return ActiveWindow(
            address=address,
            app_class=window.app_class,
            application_name=application.name,
            title=window.title,
            icon=application.icon,
            fullscreen=fullscreen,
        )

    def _dispatch_workspace(
        self,
        workspace_id: int,
        workspace_name: str | None,
        is_special: bool,
    ) -> None:
        if is_special:
            expression = self._special_workspace_expression(workspace_name)
            self._dispatch_lua(expression)
            return

        for special_name in self._get_visible_special_workspace_names():
            self._dispatch_lua(self._special_workspace_expression(special_name))

        self._dispatch_lua(f"hl.dsp.focus({{ workspace = {workspace_id} }})")

    @staticmethod
    def _special_workspace_expression(workspace_name: str | None) -> str:
        if not workspace_name or workspace_name == "special:special":
            return "hl.dsp.workspace.toggle_special()"
        if workspace_name.startswith("special:"):
            suffix = workspace_name.split(":", 1)[1]
            return f'hl.dsp.workspace.toggle_special("{suffix}")'
        return f'hl.dsp.focus({{ workspace = "{workspace_name}" }})'

    def _get_visible_special_workspace_names(self) -> tuple[str, ...]:
        try:
            monitors = self._json("monitors")
        except HyprlandError:
            return ()
        if not isinstance(monitors, list):
            return ()
        names: list[str] = []
        for monitor in monitors:
            special = (monitor.get("specialWorkspace") or {}).get("name")
            if special:
                names.append(str(special))
        return tuple(names)

    def _special_workspace_is_visible(self, workspace_name: str) -> bool:
        return workspace_name in self._get_visible_special_workspace_names()

    @staticmethod
    def _dispatch_lua(expression: str) -> None:
        try:
            subprocess.run(
                ["hyprctl", "dispatch", expression],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            stderr = ""
            if isinstance(error, subprocess.CalledProcessError):
                stderr = (error.stderr or error.stdout or "").strip()
            detail = f": {stderr}" if stderr else ""
            raise HyprlandError(f"could not execute Hyprland dispatch{detail}") from error

    @staticmethod
    def _json(command: str) -> Any:
        try:
            completed = subprocess.run(
                ["hyprctl", "-j", command],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            return json.loads(completed.stdout)
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            raise HyprlandError(f"could not read Hyprland {command}") from error

    @staticmethod
    def _event_socket_path() -> Path:
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not signature:
            raise HyprlandError("HYPRLAND_INSTANCE_SIGNATURE is not set")
        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        return runtime_dir / "hypr" / signature / ".socket2.sock"
