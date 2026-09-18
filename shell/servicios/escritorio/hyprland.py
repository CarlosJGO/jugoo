"""Single source of truth for Hyprland IPC state and high-level shell events."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any

from ...eventbus import EventBus
from ...icons import DESKTOP_ICON, application_for_window, icon_for_window
from ...models import (
    ActiveWindow,
    FloatingClient,
    HyprlandSnapshot,
    Window,
    WorkspaceRecord,
    compose_workspaces,
    pick_temporary_workspace_id,
    plan_workspace_content_moves,
    with_active_workspace,
    with_focused_window,
)


WORKSPACE_CHANGED = "workspace_changed"
WORKSPACE_REQUESTED = "workspace_requested"
WORKSPACE_REORDER_REQUESTED = "workspace_reorder_requested"
ACTIVE_WINDOW_CHANGED = "active_window_changed"
WINDOW_OPENED = "window_opened"
WINDOW_CLOSED = "window_closed"
WINDOW_FOCUS_REQUESTED = "window_focus_requested"
FULLSCREEN_CHANGED = "fullscreen_changed"
MONITOR_CHANGED = "monitor_changed"
FLOATING_LAYOUT_CHANGED = "floating_layout_changed"


class HyprlandError(RuntimeError):
    """Raised when the compositor IPC cannot provide a valid answer."""


class HyprlandService:
    """Owns one socket listener, the current snapshot, and all Hyprland commands."""

    def __init__(self, event_bus: EventBus, persistent_workspaces: int | None) -> None:
        self._event_bus = event_bus
        self._persistent_workspaces = persistent_workspaces
        self._snapshot: HyprlandSnapshot | None = None
        self._snapshot_lock = threading.RLock()
        self._floating_clients: tuple[FloatingClient, ...] = ()
        self._active_workspace_id = 0
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
        self._event_bus.subscribe(
            WORKSPACE_REORDER_REQUESTED,
            self._on_workspace_reorder_requested,
            on_main=False,
        )
        self._event_bus.subscribe(
            WINDOW_FOCUS_REQUESTED,
            self._on_window_focus_requested,
            on_main=False,
        )

    @property
    def snapshot(self) -> HyprlandSnapshot | None:
        with self._snapshot_lock:
            return self._snapshot

    @property
    def floating_clients(self) -> tuple[FloatingClient, ...]:
        with self._snapshot_lock:
            return self._floating_clients

    @property
    def active_workspace_id(self) -> int:
        with self._snapshot_lock:
            return self._active_workspace_id

    def poll_floating_layout(self) -> tuple[FloatingClient, ...]:
        """Refresh floating client geometry (used while dragging near the bar)."""
        try:
            clients_raw = self._json("clients")
            active_workspace_id = int(self._json("activeworkspace").get("id", 0))
        except HyprlandError:
            return self.floating_clients
        clients = self._floating_clients_from_raw(clients_raw)
        with self._snapshot_lock:
            changed = (
                clients != self._floating_clients
                or active_workspace_id != self._active_workspace_id
            )
            self._floating_clients = clients
            self._active_workspace_id = active_workspace_id
        if changed:
            self._event_bus.emit(FLOATING_LAYOUT_CHANGED, clients)
        return clients

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
        self._event_bus.unsubscribe(
            WORKSPACE_REORDER_REQUESTED,
            self._on_workspace_reorder_requested,
        )
        self._event_bus.unsubscribe(WINDOW_FOCUS_REQUESTED, self._on_window_focus_requested)
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

    def _on_window_focus_requested(self, address: Any) -> None:
        """Focus a mapped client off GTK's main thread."""
        if self._stop_event.is_set() or not isinstance(address, str) or not address.strip():
            return

        def worker() -> None:
            try:
                self._focus_window(address.strip())
            except HyprlandError as error:
                print(f"shell: {error}")
            finally:
                with self._command_threads_lock:
                    self._command_threads.discard(threading.current_thread())

        command_thread = threading.Thread(
            target=worker,
            name="hyprland-window-focus",
            daemon=True,
        )
        with self._command_threads_lock:
            self._command_threads.add(command_thread)
        command_thread.start()

    def _focus_window(self, address: str) -> None:
        escaped_address = address.replace("\\", "\\\\").replace('"', '\\"')
        self._dispatch_lua(f'hl.dsp.focus({{ window = "address:{escaped_address}" }})')

    def _on_workspace_reorder_requested(self, payload: Any) -> None:
        """Move or swap windows between two normal workspaces off GTK's thread."""
        if self._stop_event.is_set() or not isinstance(payload, dict):
            return
        try:
            source_workspace = int(payload["source_workspace"])
            target_workspace = int(payload["target_workspace"])
        except (KeyError, TypeError, ValueError):
            return
        if source_workspace < 1 or target_workspace < 1 or source_workspace == target_workspace:
            return

        def worker() -> None:
            try:
                self._move_workspace_contents(source_workspace, target_workspace)
            except (HyprlandError, TypeError, ValueError) as error:
                print(f"shell: {error}")
            finally:
                with self._command_threads_lock:
                    self._command_threads.discard(threading.current_thread())

        command_thread = threading.Thread(
            target=worker,
            name="hyprland-workspace-content-move",
            daemon=True,
        )
        with self._command_threads_lock:
            self._command_threads.add(command_thread)
        command_thread.start()

    def _move_workspace_contents(self, source_id: int, target_id: int) -> None:
        clients = self._json("clients")
        if not isinstance(clients, list):
            return

        temporary_id = pick_temporary_workspace_id(
            (client.get("workspace") or {}).get("id") for client in clients
        )
        moves = plan_workspace_content_moves(
            source_id,
            target_id,
            self._workspace_addresses(clients, source_id),
            self._workspace_addresses(clients, target_id),
            temporary_id,
        )
        for address, workspace_id in moves:
            self._move_window(address, workspace_id)
        if moves:
            time.sleep(0.05)
        self._dispatch_workspace(target_id, str(target_id), False)

    @staticmethod
    def _workspace_addresses(clients: list[dict[str, Any]], workspace_id: int) -> tuple[str, ...]:
        return tuple(
            str(client["address"])
            for client in clients
            if client.get("mapped")
            and not client.get("hidden")
            and client.get("address")
            and (client.get("workspace") or {}).get("id") == workspace_id
            and not client.get("pinned")
        )

    def _move_window(self, address: str, workspace: int) -> None:
        escaped_address = address.replace("\\", "\\\\").replace('"', '\\"')
        self._dispatch_lua(
            'hl.dsp.window.move({'
            f'window="address:{escaped_address}", '
            f'workspace="{workspace}", '
            'follow=false'
            '})'
        )

    def _handle_workspace_requested(self, workspace_id: Any) -> None:
        try:
            requested_id = int(workspace_id)
        except (TypeError, ValueError) as error:
            print(f"shell: {error}")
            return

        snapshot = self.snapshot
        workspace_name: str | None = None
        is_special = False
        if snapshot is not None:
            for workspace in snapshot.workspaces:
                if workspace.id == requested_id:
                    workspace_name = workspace.name
                    is_special = workspace.is_special
                    if workspace.active and not is_special:
                        if not self._get_visible_special_workspace_names():
                            return
                    break
        if workspace_name is None:
            from ...ui.workspace_accents import is_hypr_special_name

            # Fallback when the strip asked for an id not in the last snapshot.
            is_special = requested_id < 0 and is_hypr_special_name(str(requested_id))

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
                    try:
                        snapshot = self._refresh_full()
                        self._emit(WORKSPACE_CHANGED, snapshot)
                        self._emit_active_window(snapshot)
                    except HyprlandError as error:
                        print(f"shell: {error}")
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
            elif event in {
                "movewindow",
                "movewindowv2",
                "createworkspace",
                "destroyworkspace",
                "renameworkspace",
                "urgent",
                "changefloatingmode",
            }:
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
        clients_raw = self._json("clients")
        records = tuple(self._workspace(item) for item in self._json("workspaces"))
        windows = tuple(self._window(item) for item in clients_raw)
        active_workspace_id = int(self._json("activeworkspace").get("id", 0))
        active_window = self._active_window(self._json("activewindow"))
        floating = self._floating_clients_from_raw(clients_raw)
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
        with self._snapshot_lock:
            floating_changed = (
                floating != self._floating_clients
                or active_workspace_id != self._active_workspace_id
            )
            self._floating_clients = floating
            self._active_workspace_id = active_workspace_id
        replaced = self._replace_snapshot(snapshot)
        if floating_changed:
            self._event_bus.emit(FLOATING_LAYOUT_CHANGED, floating)
        return replaced

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
        from ...ui.workspace_accents import is_hypr_special_name

        workspace_id = int(item["id"])
        name = str(item.get("name", item["id"]))
        return WorkspaceRecord(
            id=workspace_id,
            name=name,
            # Named workspaces (e.g. gaming) can hash to negative ids; only
            # Hypr ``special:…`` scratchpads are true specials for toggle.
            is_special=is_hypr_special_name(name),
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
    def _floating_clients_from_raw(items: Any) -> tuple[FloatingClient, ...]:
        if not isinstance(items, list):
            return ()
        clients: list[FloatingClient] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("floating")):
                continue
            if not bool(item.get("mapped", True)):
                continue
            if bool(item.get("hidden", False)):
                continue
            # Skip clients Hyprland marks as not currently visible on a monitor.
            if item.get("visible") is False:
                continue
            at = item.get("at") or [0, 0]
            size = item.get("size") or [0, 0]
            try:
                x, y = int(at[0]), int(at[1])
                width, height = int(size[0]), int(size[1])
            except (TypeError, ValueError, IndexError):
                continue
            workspace = item.get("workspace") or {}
            try:
                workspace_id = int(workspace.get("id", 0))
            except (TypeError, ValueError):
                workspace_id = 0
            try:
                monitor = int(item.get("monitor", 0) or 0)
            except (TypeError, ValueError):
                monitor = 0
            try:
                fullscreen = int(item.get("fullscreen", 0) or 0)
            except (TypeError, ValueError):
                fullscreen = 0
            clients.append(
                FloatingClient(
                    address=str(item.get("address", "")),
                    app_class=str(item.get("class", "")),
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    workspace_id=workspace_id,
                    monitor=monitor,
                    fullscreen=fullscreen,
                    pinned=bool(item.get("pinned", False)),
                )
            )
        clients.sort(key=lambda client: client.address)
        return tuple(clients)

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
        maximized = int(item.get("maximized", 0) or 0)
        return ActiveWindow(
            address=address,
            app_class=window.app_class,
            application_name=application.name,
            title=window.title,
            icon=application.icon,
            fullscreen=fullscreen,
            maximized=maximized,
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

