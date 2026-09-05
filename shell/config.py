"""User-editable shell configuration that cannot be expressed in GTK CSS."""

from __future__ import annotations


# ``None`` mirrors Hyprland exactly. Set an integer to retain empty targets 1..N.
PERSISTENT_WORKSPACES: int | None = None

# Number of consecutive Hyprland workspaces represented by one draggable block.
WORKSPACES_PER_BLOCK = 3

# Layer-shell top inset. 0 anchors the bar flush with the monitor edge (y=0).
TOP_MARGIN = 0

# Screen-edge padding when clamping anchored popups.
POPUP_EDGE_MARGIN = 8

# Grace period before closing a popup after the pointer leaves it.
POPUP_OUTSIDE_DISMISS_GRACE_MS = 500

# Volume OSD auto-hide delay after the last default-sink volume/mute/output change.
VOLUME_OSD_HIDE_DELAY_MS = 200

# Hyprland activewindow.fullscreen value for MainMod+D (hl.dsp.window.fullscreen mode 1).
HYPRLAND_MAXIMIZED_FULLSCREEN = 1

# GTK 3 exposes these through widget APIs rather than its CSS property set.
WORKSPACE_BUTTON_SPACING = 4
APPLICATION_ICON_SPACING = 6
APPLICATION_ICON_SIZE = 18
FOCUSED_APPLICATION_ICON_SIZE = 21
ACTIVE_WINDOW_ICON_SIZE = 24
ACTIVE_WINDOW_CONTENT_SPACING = 8
# Stable pixel width for the active-window block (title length must not affect bar layout).
ACTIVE_WINDOW_WIDTH = 280
WORKSPACE_VISIBLE_ICON_LIMIT = 3
WORKSPACE_HOVER_DELAY_MS = 200
WORKSPACE_HOVER_GRACE_MS = 350
WORKSPACE_POPUP_OFFSET = 6
AUDIO_POLL_INTERVAL_SEC = 0.5

# Clock display formats (strftime).
CLOCK_TIME_FORMAT = "%I:%M %p"
CLOCK_DATE_FORMAT = "%a · %d %b"

# System statistics module. Thermal thresholds live in shell/servicios/sistema/system.py.
SYSTEM_STATS_UPDATE_INTERVAL = 1
STATS_SECTION_SPACING = 12
STATS_CPU_BAR_WIDTH = 84
STATS_CPU_BAR_TEMP_MIN_C = 30.0
STATS_CPU_BAR_TEMP_MAX_C = 95.0
STATS_GPU_FAN_ICON_SIZE = 20

# System tray module.
TRAY_ICON_SIZE = 16
TRAY_ICON_PADDING = 4
TRAY_SLOT_SIZE = TRAY_ICON_SIZE + (TRAY_ICON_PADDING * 2)
TRAY_ITEM_SPACING = 4
TRAY_COMPACT_ICON_SIZE = 12
TRAY_COMPACT_SLOT_SIZE = TRAY_COMPACT_ICON_SIZE + 4

# Network module (NetworkManager-backed service).
NETWORK_ICON_SIZE = 16
NETWORK_COMPACT_ICON_SIZE = 12
NETWORK_REFRESH_DEBOUNCE_MS = 200
NETWORK_FALLBACK_POLL_SEC = 30
NETWORK_CONNECTIVITY_FOLLOWUP_DELAYS_MS = (200, 800, 2000, 5000)
NETWORK_WIFI_SCAN_FOLLOWUP_DELAYS_MS = (300, 1200, 3000)

# Control center popup.
CONTROL_CENTER_POPUP_OFFSET = 6
CONTROL_CENTER_POPUP_WIDTH = 400
CONTROL_CENTER_POPUP_MAX_HEIGHT = 560

# Power menu module.
POWER_ICON_SIZE = 16
POWER_COMPACT_ICON_SIZE = 12
POWER_MENU_OFFSET = 6

# Notifications D-Bus server (org.freedesktop.Notifications).
NOTIFICATIONS_BUS_NAME = "org.freedesktop.Notifications"
NOTIFICATIONS_DEV_BUS_NAME = "org.freedesktop.Notifications.ShellDev"
NOTIFICATIONS_OBJECT_PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS_INTERFACE = "org.freedesktop.Notifications"
NOTIFICATIONS_PRODUCTION_RETRY_SEC = 5

# Bar button.
NOTIFICATION_ICON_SIZE = 16
NOTIFICATION_COMPACT_ICON_SIZE = 12
NOTIFICATIONS_BELL_ANIMATION_MS = 650

# History persistence.
NOTIFICATIONS_HISTORY_PATH = "notifications.json"
NOTIFICATIONS_MAX_HISTORY = 200
NOTIFICATIONS_PAUSED_DEFAULT = False

# Popup list.
NOTIFICATION_POPUP_OFFSET = 10
NOTIFICATION_POPUP_WIDTH = 440
NOTIFICATION_POPUP_MAX_HEIGHT = 540
NOTIFICATION_POPUP_ICON_SIZE = 20
NOTIFICATION_POPUP_ROW_BODY_LINES = 4
NOTIFICATION_POPUP_LIST_SPACING = 8

# Timeouts (milliseconds). 0 = persist until manually dismissed.
NOTIFICATIONS_DEFAULT_TIMEOUT_MS = 5000
NOTIFICATIONS_CRITICAL_TIMEOUT_MS = 15000
NOTIFICATIONS_CRITICAL_PERSIST = True

# Incoming toast (separate from history popup).
NOTIFICATIONS_TOAST_ENABLED = True
NOTIFICATIONS_MAX_VISIBLE_TOASTS = 3
NOTIFICATIONS_TOAST_STACK_STEP = 88
NOTIFICATIONS_TOAST_WIDTH = 400
NOTIFICATIONS_TOAST_TIMEOUT_MS = 5000

# Sound (non-blocking; shell works without the file).
NOTIFICATIONS_SOUND_ENABLED = True
NOTIFICATIONS_SOUND_PATH = "assets/notification.ogg"

# Icon cache for image-data hints (outside the config tree).
NOTIFICATIONS_ICON_CACHE_DIR = ".cache/waybar-shell/notification-icons"

# MPRIS media service.
MEDIA_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MEDIA_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MEDIA_PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
MEDIA_ROOT_INTERFACE = "org.mpris.MediaPlayer2"
MEDIA_DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"
MEDIA_REFRESH_DEBOUNCE_MS = 150
MEDIA_TRACK_CHANGE_REFRESH_MS = 300
MEDIA_POSITION_POLL_MS = 1000
MEDIA_ARTWORK_CACHE_DIR = ".cache/waybar-shell/media-artwork"
MEDIA_ARTWORK_DOWNLOAD_TIMEOUT_SEC = 8
MEDIA_POPUP_OFFSET = 6
MEDIA_POPUP_WIDTH = 440
MEDIA_POPUP_MAX_HEIGHT = 300
MEDIA_ARTWORK_SIZE = 128

# Pinned application dock (in-bar; expansion uses Gtk.Revealer, not a popup).
PINNED_APPS_VISIBLE_LIMIT = 9
PINNED_APP_ICON_SIZE = 20
PINNED_APP_COMPACT_ICON_SIZE = 16
PINNED_APP_SPACING = 2
PINNED_DOCK_REVEAL_MS = 180
PINNED_APPS_PATH = "pinned-apps.json"

# Application launcher overlay (Super+Space).
LAUNCHER_WIDTH = 440
LAUNCHER_MAX_HEIGHT = 520
LAUNCHER_ROW_ICON_SIZE = 28
LAUNCHER_LIST_SPACING = 2

# Audio visualizer (PipeWire monitor sampling for the active-window block).
AUDIO_VISUALIZER_BAR_COUNT = 14
AUDIO_VISUALIZER_FPS = 14
AUDIO_VISUALIZER_INTERVAL_MS = 1000 // AUDIO_VISUALIZER_FPS
AUDIO_VISUALIZER_PCM_RATE = 16000
AUDIO_VISUALIZER_PCM_CHANNELS = 1
AUDIO_VISUALIZER_PCM_LATENCY = 256
