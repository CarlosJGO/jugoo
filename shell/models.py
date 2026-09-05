"""Immutable state shared between shell services and visual modules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Sequence


@dataclass(frozen=True)
class Window:
    address: str
    app_class: str
    title: str
    workspace_id: int
    application_name: str = ""
    icon: str = ""
    pid: int | None = None


@dataclass(frozen=True)
class Workspace:
    """The complete renderable state of one workspace button."""

    id: int
    name: str
    active: bool
    windows: tuple[Window, ...]
    icons: tuple[str, ...]
    focused_window_address: str | None
    is_special: bool = False


@dataclass(frozen=True)
class WorkspaceBlock:
    """Visual group of consecutive workspaces; workspace IDs and contents stay unchanged."""

    block_index: int
    workspaces: tuple[Workspace, ...]


@dataclass(frozen=True)
class WorkspaceRecord:
    """Hyprland's workspace metadata before it is composed for display."""

    id: int
    name: str
    is_special: bool = False


@dataclass(frozen=True)
class ActiveWindow:
    address: str
    app_class: str
    application_name: str
    title: str
    icon: str
    fullscreen: int = 0


@dataclass(frozen=True)
class HyprlandSnapshot:
    """The service-owned state exposed to modules through high-level events."""

    workspaces: tuple[Workspace, ...]
    active_window: ActiveWindow


@dataclass(frozen=True)
class AudioDevice:
    """PipeWire/PulseAudio sink or source exposed via pactl."""

    id: str
    name: str
    description: str
    kind: str  # "output" | "input"


@dataclass(frozen=True)
class AudioStream:
    """Represents a single active audio stream (playback or capture)."""

    id: str
    application_name: str
    title: str
    icon: str
    volume: float
    is_muted: bool
    is_playing: bool
    stream_kind: str  # "playback" | "capture"
    device_id: str
    device_name: str
    workspace_id: int | None = None
    window_address: str | None = None


@dataclass(frozen=True)
class WorkspaceAudioState:
    """Per-workspace audio summary shared between indicators, popup, and panel."""

    workspace_id: int
    has_audio: bool
    is_playing: bool
    has_muted: bool
    streams: tuple[AudioStream, ...]


@dataclass(frozen=True)
class SystemVolumeState:
    """Default sink volume/mute used by the global volume OSD."""

    sink_name: str
    sink_description: str
    volume: float
    is_muted: bool

    @property
    def percent(self) -> int:
        return int(round(max(0.0, min(1.0, self.volume)) * 100))


@dataclass(frozen=True)
class AudioSnapshot:
    """Service-owned audio state exposed to modules via EventBus."""

    workspaces_audio: tuple[WorkspaceAudioState, ...]
    output_devices: tuple[AudioDevice, ...] = ()
    input_devices: tuple[AudioDevice, ...] = ()
    system_volume: SystemVolumeState | None = None

    def get_workspace_audio(self, workspace_id: int) -> WorkspaceAudioState:
        for state in self.workspaces_audio:
            if state.workspace_id == workspace_id:
                return state
        return WorkspaceAudioState(
            workspace_id=workspace_id,
            has_audio=False,
            is_playing=False,
            has_muted=False,
            streams=(),
        )


@dataclass(frozen=True)
class MediaPlayerSnapshot:
    """One MPRIS media player instance."""

    bus_name: str
    identity: str
    title: str
    artist: str
    album: str
    art_url: str
    artwork_path: str
    status: str  # playing | paused | stopped
    position_usec: int
    duration_usec: int
    can_play: bool
    can_pause: bool
    can_go_next: bool
    can_go_previous: bool
    can_seek: bool

    @property
    def is_playing(self) -> bool:
        return self.status == "playing"

    @property
    def has_track(self) -> bool:
        return bool(self.title or self.artist or self.album)


@dataclass(frozen=True)
class MediaSnapshot:
    """Service-owned MPRIS state exposed to modules via EventBus."""

    players: tuple[MediaPlayerSnapshot, ...] = ()
    active_player: str | None = None

    @property
    def active(self) -> MediaPlayerSnapshot | None:
        if self.active_player is None:
            return None
        for player in self.players:
            if player.bus_name == self.active_player:
                return player
        return None

    @property
    def has_media(self) -> bool:
        active = self.active
        return active is not None and (active.has_track or active.is_playing)

    @staticmethod
    def empty() -> "MediaSnapshot":
        return MediaSnapshot()


@dataclass(frozen=True)
class AudioVisualizerSnapshot:
    """Bar heights and colors for the active-window spectrum visualizer."""

    visible: bool
    bars: tuple[float, ...] = ()
    peaks: tuple[float, ...] = ()
    colors: tuple[tuple[float, float, float, float], ...] = ()

    @staticmethod
    def hidden(bar_count: int = 8) -> "AudioVisualizerSnapshot":
        zeros = tuple(0.0 for _ in range(bar_count))
        return AudioVisualizerSnapshot(
            visible=False,
            bars=zeros,
            peaks=zeros,
            colors=tuple((0.0, 0.0, 0.0, 0.0) for _ in range(bar_count)),
        )


@dataclass(frozen=True)
class NetworkConnectivitySnapshot:
    """Normalized Internet/connectivity state from NetworkManager."""

    level: str

    @staticmethod
    def from_nm_label(label: str) -> "NetworkConnectivitySnapshot":
        normalized = label if label in {"full", "limited", "portal", "none", "unknown"} else "unknown"
        return NetworkConnectivitySnapshot(level=normalized)

    @property
    def has_internet(self) -> bool:
        return self.level == "full"

    @property
    def summary_label(self) -> str:
        return {
            "full": "Internet",
            "limited": "Sin Internet",
            "portal": "Portal cautivo",
            "none": "Sin Internet",
            "unknown": "Desconocido",
        }.get(self.level, "Desconocido")


@dataclass(frozen=True)
class NetworkInterfaceSnapshot:
    """Immutable view of one network interface for bar modules and control center."""

    interface: str
    device_type: str
    state: str
    connection_name: str
    ip_address: str
    link_up: bool
    speed_mbps: int | None
    mac_address: str
    device_path: str = ""

    @property
    def connected(self) -> bool:
        return self.state == "connected"


@dataclass(frozen=True)
class WifiAccessPointSnapshot:
    """One scanned Wi-Fi access point."""

    path: str
    ssid: str
    strength: int
    secured: bool
    frequency_mhz: int
    active: bool = False


@dataclass(frozen=True)
class HotspotClientSnapshot:
    """One station associated with the local AP, as reported by NetworkManager."""

    mac_address: str


@dataclass(frozen=True)
class WifiHotspotCapabilities:
    """Adapter flags from NetworkManager Device.Wireless.WirelessCapabilities."""

    supports_ap: bool = False
    supports_2_4ghz: bool = False
    supports_5ghz: bool = False


@dataclass(frozen=True)
class HotspotSnapshot:
    """Hotspot state derived from NetworkManager. Never includes the PSK."""

    status: str
    available: bool = False
    supports_ap: bool = False
    supports_2_4ghz: bool = False
    supports_5ghz: bool = False
    active: bool = False
    ssid: str = ""
    band: str = "auto"
    password_configured: bool = False
    wifi_device: str = ""
    shared_connection: bool = False
    ipv4_shared: bool = False
    forwarding_enabled: bool = False
    ethernet_upstream: bool = False
    connected_clients: tuple[HotspotClientSnapshot, ...] = ()
    error_message: str = ""
    connection_path: str = ""

    @staticmethod
    def empty() -> "HotspotSnapshot":
        return HotspotSnapshot(status="wifi_unavailable")


@dataclass(frozen=True)
class NetworkSnapshot:
    """Service-owned network state exposed to modules via EventBus."""

    ethernet: NetworkInterfaceSnapshot | None
    wifi: NetworkInterfaceSnapshot | None
    primary: NetworkInterfaceSnapshot | None
    connectivity: NetworkConnectivitySnapshot
    wireless_enabled: bool = False
    wireless_hardware_enabled: bool = True
    wifi_access_points: tuple[WifiAccessPointSnapshot, ...] = ()
    wifi_connection_target: str = ""
    wifi_connection_error: str = ""
    hotspot: HotspotSnapshot = field(default_factory=HotspotSnapshot.empty)

    @staticmethod
    def empty() -> "NetworkSnapshot":
        return NetworkSnapshot(
            ethernet=None,
            wifi=None,
            primary=None,
            connectivity=NetworkConnectivitySnapshot(level="unknown"),
            wireless_enabled=False,
            wireless_hardware_enabled=True,
            wifi_access_points=(),
            wifi_connection_target="",
            wifi_connection_error="",
            hotspot=HotspotSnapshot.empty(),
        )


@dataclass(frozen=True)
class NotificationAction:
    """One actionable button exposed by a desktop notification."""

    key: str
    label: str


@dataclass(frozen=True)
class NotificationSnapshot:
    """Immutable view of one stored notification for widgets and tests."""

    id: int
    app_name: str
    app_icon: str
    summary: str
    body: str
    actions: tuple[NotificationAction, ...]
    urgency: int
    timestamp: float
    expire_timeout_ms: int
    read: bool = False
    dismissed: bool = False
    expired: bool = False
    icon_name: str = ""
    image_path: str = ""
    desktop_entry: str = ""


def reorder_workspace_order(
    workspaces: Sequence[Workspace],
    source_id: int,
    target_id: int,
) -> tuple[Workspace, ...]:
    """Move a workspace block to a new position while keeping the IDs attached to each block."""
    if source_id == target_id:
        return tuple(workspaces)

    ordered = list(workspaces)
    source_index = next((index for index, workspace in enumerate(ordered) if workspace.id == source_id), None)
    target_index = next((index for index, workspace in enumerate(ordered) if workspace.id == target_id), None)
    if source_index is None or target_index is None:
        return tuple(workspaces)

    workspace = ordered.pop(source_index)
    if source_index < target_index:
        target_index -= 1
    ordered.insert(target_index, workspace)
    return tuple(ordered)


def compose_workspace_blocks(
    workspaces: Iterable[Workspace],
    workspaces_per_block: int,
) -> tuple[WorkspaceBlock, ...]:
    """Group regular workspaces by their stable Hyprland IDs."""
    if workspaces_per_block < 1:
        raise ValueError("workspaces_per_block must be positive")
    grouped: dict[int, list[Workspace]] = {}
    for workspace in workspaces:
        if workspace.is_special or workspace.id < 1:
            continue
        block_index = (workspace.id - 1) // workspaces_per_block
        grouped.setdefault(block_index, []).append(workspace)
    return tuple(
        WorkspaceBlock(index, tuple(grouped[index]))
        for index in sorted(grouped)
    )


def swap_workspace_blocks(
    blocks: Sequence[WorkspaceBlock],
    source_index: int,
    target_index: int,
) -> tuple[WorkspaceBlock, ...]:
    """Swap complete block objects without changing their workspace contents."""
    if source_index == target_index:
        return tuple(blocks)
    ordered = list(blocks)
    source_position = next(
        (position for position, block in enumerate(ordered) if block.block_index == source_index),
        None,
    )
    target_position = next(
        (position for position, block in enumerate(ordered) if block.block_index == target_index),
        None,
    )
    if source_position is None or target_position is None:
        return tuple(blocks)
    ordered[source_position], ordered[target_position] = (
        ordered[target_position],
        ordered[source_position],
    )
    return tuple(ordered)


def pick_temporary_workspace_id(
    existing_ids: Iterable[int | None],
    *,
    start: int = 9999,
) -> int:
    """Choose a numeric workspace that no mapped client currently occupies."""
    occupied = {
        workspace_id
        for workspace_id in existing_ids
        if isinstance(workspace_id, int)
    }
    temporary_id = start
    while temporary_id in occupied:
        temporary_id -= 1
    return temporary_id


def plan_workspace_content_moves(
    current_id: int,
    target_id: int,
    current_addresses: Sequence[str],
    target_addresses: Sequence[str],
    temporary_id: int,
) -> tuple[tuple[str, int], ...]:
    """Window address → workspace moves matching ``move_workspace_contents.py``.

    Occupied destination uses a three-step swap through ``temporary_id`` so the
    two groups never mix. Empty destination is a direct move. Empty source
    produces no moves; the caller just focuses the target, matching
    ``move_workspace_contents.py``.
    """
    if current_id == target_id or current_id < 1 or target_id < 1:
        return ()
    if not current_addresses:
        return ()
    if not target_addresses:
        return tuple((address, target_id) for address in current_addresses)
    return (
        tuple((address, temporary_id) for address in current_addresses)
        + tuple((address, current_id) for address in target_addresses)
        + tuple((address, target_id) for address in current_addresses)
    )


def compose_workspaces(
    records: Iterable[WorkspaceRecord],
    windows: Iterable[Window],
    active_workspace_id: int,
    persistent_workspaces: int | None,
    icon_for_window: Callable[[Window], str],
    focused_window_address: str | None,
) -> tuple[Workspace, ...]:
    """Build the view model once, keeping GTK independent of Hyprland data."""
    known = {record.id: record for record in records}
    windows_by_workspace: dict[int, list[Window]] = {}
    for window in windows:
        if window.workspace_id != 0:
            windows_by_workspace.setdefault(window.workspace_id, []).append(window)

    regular_ids = {
        workspace_id
        for workspace_id, record in known.items()
        if not record.is_special and workspace_id > 0
    }
    if persistent_workspaces is not None:
        regular_ids.update(range(1, persistent_workspaces + 1))

    special_ids = {
        workspace_id
        for workspace_id, record in known.items()
        if record.is_special and windows_by_workspace.get(workspace_id)
    }
    special_ids.update(
        workspace_id
        for workspace_id, windows in windows_by_workspace.items()
        if workspace_id < 0 and windows
    )

    return tuple(
        _workspace_model(
            known.get(workspace_id, WorkspaceRecord(workspace_id, str(workspace_id), False)),
            windows_by_workspace.get(workspace_id, ()),
            workspace_id == active_workspace_id,
            focused_window_address,
            icon_for_window,
        )
        for workspace_id in sorted(regular_ids)
    ) + tuple(
        _workspace_model(
            known.get(
                workspace_id,
                WorkspaceRecord(workspace_id, str(workspace_id), workspace_id < 0),
            ),
            windows_by_workspace[workspace_id],
            workspace_id == active_workspace_id,
            focused_window_address,
            icon_for_window,
        )
        for workspace_id in sorted(special_ids, key=lambda item: (known.get(item, WorkspaceRecord(item, str(item), True)).name, item))
    )


def with_active_workspace(
    workspaces: Sequence[Workspace], active_workspace_id: int
) -> tuple[Workspace, ...]:
    """Return the same objects except for buttons whose active state changed."""
    return tuple(
        workspace
        if workspace.active == (workspace.id == active_workspace_id)
        else replace(workspace, active=workspace.id == active_workspace_id)
        for workspace in workspaces
    )


def with_focused_window(
    workspaces: Sequence[Workspace], focused_window_address: str | None
) -> tuple[Workspace, ...]:
    """Change only workspace models whose rendered focused icon changed."""
    return tuple(_with_workspace_focus(workspace, focused_window_address) for workspace in workspaces)


def _workspace_model(
    record: WorkspaceRecord,
    windows: Iterable[Window],
    active: bool,
    focused_window_address: str | None,
    icon_for_window: Callable[[Window], str],
) -> Workspace:
    ordered_windows = _focus_first(tuple(windows), focused_window_address)
    focused_address = next(
        (window.address for window in ordered_windows if window.address == focused_window_address), None
    )
    return Workspace(
        id=record.id,
        name=record.name,
        active=active,
        windows=ordered_windows,
        icons=tuple(window.icon or icon_for_window(window) for window in ordered_windows),
        focused_window_address=focused_address,
        is_special=record.is_special,
    )


def _with_workspace_focus(workspace: Workspace, focused_window_address: str | None) -> Workspace:
    ordered_windows = _focus_first(workspace.windows, focused_window_address)
    focused_address = next(
        (window.address for window in ordered_windows if window.address == focused_window_address), None
    )
    ordered_icons = tuple(
        icon
        for _window, icon in sorted(
            zip(workspace.windows, workspace.icons),
            key=lambda item: item[0].address != focused_window_address,
        )
    )
    if (
        workspace.focused_window_address == focused_address
        and workspace.windows == ordered_windows
        and workspace.icons == ordered_icons
    ):
        return workspace
    return replace(
        workspace,
        windows=ordered_windows,
        icons=ordered_icons,
        focused_window_address=focused_address,
    )


def _focus_first(windows: tuple[Window, ...], focused_window_address: str | None) -> tuple[Window, ...]:
    return tuple(sorted(windows, key=lambda window: window.address != focused_window_address))


@dataclass(frozen=True)
class DesktopApplication:
    """One installed application discovered from a ``.desktop`` file."""

    id: str
    name: str
    icon: str
    exec_cmd: str = ""
    wm_class: str = ""
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    comment: str = ""
    generic_name: str = ""
    terminal: bool = False
    desktop_path: str = ""
    new_instance_exec: str = ""


@dataclass(frozen=True)
class ApplicationsSnapshot:
    """Service-owned application catalog, dock pins, and launcher favorites."""

    applications: tuple[DesktopApplication, ...] = ()
    pinned_ids: tuple[str, ...] = ()
    favorite_ids: tuple[str, ...] = ()

    def app_by_id(self, app_id: str) -> DesktopApplication | None:
        wanted = normalize_desktop_id(app_id)
        if not wanted:
            return None
        for application in self.applications:
            if application.id == wanted:
                return application
        return None

    def pinned_apps(self) -> tuple[DesktopApplication, ...]:
        apps: list[DesktopApplication] = []
        for app_id in self.pinned_ids:
            application = self.app_by_id(app_id)
            if application is not None:
                apps.append(application)
        return tuple(apps)

    def is_pinned(self, app_id: str) -> bool:
        return normalize_desktop_id(app_id) in self.pinned_ids

    def is_favorite(self, app_id: str) -> bool:
        return normalize_desktop_id(app_id) in self.favorite_ids


TASK_REPEAT_NONE = "none"
TASK_REPEAT_DAILY = "daily"
TASK_REPEAT_MONTHLY = "monthly"
TASK_REPEATS = frozenset({TASK_REPEAT_NONE, TASK_REPEAT_DAILY, TASK_REPEAT_MONTHLY})

TASK_STATUS_PENDING = "pending"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_OVERDUE = "overdue"
TASK_STATUS_MISSED = "missed"


@dataclass(frozen=True)
class TaskRecord:
    """Persisted task definition plus occurrence history for recurring work."""

    id: str
    title: str
    notes: str = ""
    repeat: str = TASK_REPEAT_NONE
    due_date: str | None = None
    month_day: int = 1
    created_at: str = ""
    period_cursor: str = ""
    completed_periods: tuple[str, ...] = ()
    missed_periods: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskSnapshot:
    """Renderable task state for a given calendar day."""

    id: str
    title: str
    notes: str
    repeat: str
    due_date: str | None
    month_day: int
    status: str
    period_key: str
    missed_count: int
    created_at: str
    occurrence_date: str


@dataclass(frozen=True)
class TasksSnapshot:
    """Service-owned tasks as of today, used by the bar badge and the panel."""

    today: str
    tasks: tuple[TaskSnapshot, ...] = ()
    overdue_count: int = 0
    pending_today_count: int = 0


def normalize_desktop_id(value: str) -> str:
    """Strip ``.desktop`` and normalize an application identifier."""
    ident = value.strip()
    if ident.casefold().endswith(".desktop"):
        ident = ident[: -len(".desktop")]
    return ident


def pin_application(pinned_ids: Sequence[str], app_id: str) -> tuple[str, ...]:
    """Append ``app_id`` if missing; keep existing order otherwise."""
    ident = normalize_desktop_id(app_id)
    if not ident:
        return tuple(pinned_ids)
    ordered = tuple(normalize_desktop_id(item) for item in pinned_ids if normalize_desktop_id(item))
    if ident in ordered:
        return ordered
    return ordered + (ident,)


def unpin_application(pinned_ids: Sequence[str], app_id: str) -> tuple[str, ...]:
    """Remove ``app_id`` and close the gap; other entries keep their relative order."""
    ident = normalize_desktop_id(app_id)
    return tuple(
        item
        for item in (normalize_desktop_id(entry) for entry in pinned_ids)
        if item and item != ident
    )


def move_pinned_application(
    pinned_ids: Sequence[str],
    source_id: str,
    target_id: str = "",
    *,
    at_index: int | None = None,
) -> tuple[str, ...]:
    """Move ``source_id`` onto ``target_id``'s slot; unique ids stay intact.

    After removal the source is inserted at the target's original index, so
    adjacent swaps work in both directions. ``at_index`` places the source at
    that final index (used to send an icon into overflow).
    """
    ident_source = normalize_desktop_id(source_id)
    ordered = [normalize_desktop_id(item) for item in pinned_ids if normalize_desktop_id(item)]
    if not ident_source or ident_source not in ordered:
        return tuple(ordered)
    source_index = ordered.index(ident_source)
    if at_index is not None:
        item = ordered.pop(source_index)
        dest = max(0, min(int(at_index), len(ordered)))
        ordered.insert(dest, item)
        return tuple(ordered)
    ident_target = normalize_desktop_id(target_id)
    if not ident_target or ident_target not in ordered or ident_target == ident_source:
        return tuple(ordered)
    target_index = ordered.index(ident_target)
    item = ordered.pop(source_index)
    ordered.insert(target_index, item)
    return tuple(ordered)


def send_pinned_to_overflow(
    pinned_ids: Sequence[str],
    app_id: str,
    visible_limit: int,
) -> tuple[str, ...]:
    """Move a visible pin into overflow so the first extra takes a dock slot."""
    ident = normalize_desktop_id(app_id)
    ordered = tuple(normalize_desktop_id(item) for item in pinned_ids if normalize_desktop_id(item))
    if not ident or ident not in ordered or visible_limit < 1:
        return ordered
    if len(ordered) <= visible_limit or ordered.index(ident) >= visible_limit:
        return ordered
    return move_pinned_application(ordered, ident, at_index=visible_limit)


def split_pinned_dock(
    pinned_ids: Sequence[str],
    visible_limit: int,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Split pinned ids into the stable dock strip and the in-bar overflow."""
    ordered = tuple(normalize_desktop_id(item) for item in pinned_ids if normalize_desktop_id(item))
    if visible_limit < 1:
        return (), ordered, bool(ordered)
    if len(ordered) <= visible_limit:
        return ordered, (), False
    return ordered[:visible_limit], ordered[visible_limit:], True


def snapshot_windows(snapshot: HyprlandSnapshot | None) -> tuple[Window, ...]:
    if snapshot is None:
        return ()
    return tuple(window for workspace in snapshot.workspaces for window in workspace.windows)


def application_identity_keys(application: DesktopApplication) -> frozenset[str]:
    keys = {application.id.casefold()}
    if application.wm_class.strip():
        keys.add(application.wm_class.casefold().strip())
    if "." in application.id:
        keys.add(application.id.rsplit(".", 1)[-1].casefold())
    stem = Path(application.desktop_path).stem if application.desktop_path else ""
    if stem:
        keys.add(stem.casefold())
    return frozenset(key for key in keys if key)


def window_matches_application(window: Window, application: DesktopApplication) -> bool:
    class_key = window.app_class.casefold().strip()
    if not class_key:
        return False
    identities = application_identity_keys(application)
    if class_key in identities:
        return True
    for identity in identities:
        if len(identity) < 4:
            continue
        if identity in class_key or class_key in identity:
            return True
    return False


def windows_for_application(
    application: DesktopApplication,
    windows: Sequence[Window],
) -> tuple[Window, ...]:
    return tuple(window for window in windows if window_matches_application(window, application))


def next_window_to_focus(
    windows: Sequence[Window],
    active_address: str,
) -> Window | None:
    """Focus the first match, or cycle to the next when several windows are open."""
    if not windows:
        return None
    addresses = [window.address for window in windows if window.address]
    if not addresses:
        return windows[0]
    if active_address not in addresses:
        return windows[0]
    index = addresses.index(active_address)
    return windows[(index + 1) % len(windows)]


def filter_applications(
    applications: Sequence[DesktopApplication],
    query: str,
    favorite_ids: Sequence[str] = (),
) -> tuple[DesktopApplication, ...]:
    """Filter the in-memory catalog. Launcher favorites sort first, then prefix hits."""
    needle = " ".join(query.casefold().replace("-", " ").split())
    favorites = {normalize_desktop_id(item) for item in favorite_ids}

    def score(application: DesktopApplication) -> tuple[int, int, str]:
        name = application.name.casefold()
        ident = application.id.casefold()
        haystack = " ".join(
            part
            for part in (
                name,
                application.generic_name.casefold(),
                ident,
                " ".join(application.keywords).casefold(),
                " ".join(application.categories).casefold(),
                application.comment.casefold(),
            )
            if part
        ).replace("-", " ").replace(".", " ")
        if needle:
            if not (
                needle in haystack
                or all(token in haystack for token in needle.split())
            ):
                return (3, 0, name)
        prefix = 0 if name.startswith(needle) or ident.startswith(needle) else 1
        favorite_rank = 0 if application.id in favorites else 1
        return (favorite_rank, prefix, name)

    if not needle:
        return tuple(sorted(applications, key=score))
    matched = [application for application in applications if score(application)[0] < 3]
    return tuple(sorted(matched, key=score))


_NEW_WINDOW_FLAGS = ("--new-window", "--new-instance", "-new-window")


def new_instance_command(application: DesktopApplication) -> str:
    """Best-effort command that asks the app for a fresh window/instance."""
    if application.new_instance_exec.strip():
        return application.new_instance_exec.strip()
    command = application.exec_cmd.strip()
    if not command:
        return ""
    if any(flag in command for flag in _NEW_WINDOW_FLAGS) or application.terminal:
        return command
    return f"{command} --new-window"
