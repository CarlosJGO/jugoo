"""Active-window context from the existing Hyprland service. Auxiliary only."""

from __future__ import annotations

from threading import Lock
import time

from ....eventbus import EventBus
from ....models import ActiveWindow
from ...escritorio.hyprland import ACTIVE_WINDOW_CHANGED, HyprlandService
from .politica import ActivitySnapshot

DISTRACTION_CLASSES = frozenset({
    "steam",
    "steam_app",
    "steamwebhelper",
    "lutris",
    "heroic",
    "heroicgameslauncher",
    "gamescope",
    "wine",
    "wine64",
    "explorer.exe",
    "mpv",
    "vlc",
    "celluloid",
    "smplayer",
    "dolphin-emu",
    "pcsx2",
    "rpcs3",
    "yuzu",
    "ryujinx",
    "duckstation",
    "retroarch",
    "minecraft",
    "prismlauncher",
    "org.prismlauncher.prismlauncher",
})

BROWSER_CLASSES = frozenset({
    "firefox",
    "firefox-esr",
    "librewolf",
    "zen",
    "zen-browser",
    "chromium",
    "google-chrome",
    "brave-browser",
    "brave",
    "vivaldi",
    "microsoft-edge",
    "org.mozilla.firefox",
})

_MEDIA_TITLE_HINTS = (
    "youtube",
    "netflix",
    "twitch",
    "disney+",
    "disney plus",
    "prime video",
    "crunchyroll",
    "spotify",
    "plex",
)


def is_distraction_window(window: ActiveWindow | None) -> bool:
    if window is None or not window.app_class:
        return False
    tokens = _class_tokens(window.app_class)
    if tokens & DISTRACTION_CLASSES:
        return True
    if tokens & BROWSER_CLASSES:
        return _title_looks_like_media(window.title)
    return False


def _class_tokens(app_class: str) -> set[str]:
    raw = app_class.casefold().strip()
    tokens = {raw}
    if "." in raw:
        tokens.add(raw.rsplit(".", 1)[-1])
    if raw.startswith("steam_app"):
        tokens.add("steam_app")
    return tokens


def _title_looks_like_media(title: str) -> bool:
    lowered = title.casefold()
    return any(hint in lowered for hint in _MEDIA_TITLE_HINTS)


class ContextDetector:
    """Tracks distraction with real timestamps; Hyprland events update the window."""

    def __init__(self, event_bus: EventBus) -> None:
        self._lock = Lock()
        self._window: ActiveWindow | None = None
        self._distracted = False
        self._started_at: float | None = None
        event_bus.subscribe(
            ACTIVE_WINDOW_CHANGED,
            self._on_active_window,
            on_main=False,
        )

    def _on_active_window(self, window: object) -> None:
        if not isinstance(window, ActiveWindow):
            return
        self.observe(window, now=time.time())

    def observe(self, window: ActiveWindow | None, *, now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        distracted = is_distraction_window(window)
        with self._lock:
            self._window = window
            if distracted:
                if not self._distracted:
                    self._started_at = stamp
                self._distracted = True
            else:
                self._distracted = False
                self._started_at = None

    def snapshot(self, *, now: float | None = None) -> ActivitySnapshot:
        stamp = time.time() if now is None else now
        with self._lock:
            window = self._window
            started_at = self._started_at
            distracted = self._distracted
        duration = max(0.0, stamp - started_at) if distracted and started_at else 0.0
        app_class = window.app_class if window is not None else ""
        title = window.title if window is not None else ""
        if distracted:
            minutes = int(duration // 60)
            name = (window.application_name if window is not None else "") or app_class or "una app"
            label = f"user has been distracted in {name} for {minutes} minutes"
        else:
            label = "desktop"
        return ActivitySnapshot(
            distracted=distracted,
            distracted_for_sec=duration,
            app_class=app_class,
            title=title,
            label=label,
        )


def start_hyprland_context(
    event_bus: EventBus,
    detector: ContextDetector | None = None,
) -> tuple[HyprlandService, ContextDetector]:
    context = detector if detector is not None else ContextDetector(event_bus)
    service = HyprlandService(event_bus, None)
    service.start()
    return service, context
