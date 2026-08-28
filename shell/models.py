"""Immutable state shared between shell services and visual modules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
