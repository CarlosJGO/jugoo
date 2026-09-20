"""User-editable shell configuration that cannot be expressed in GTK CSS."""

from __future__ import annotations


# Theme catalog entry from ``themes/<name>.toml``.
ACTIVE_THEME = "space"
HYPRLAND_THEME_EXPORT_PATH = "~/.config/hypr/config/jugoo_theme_generated.lua"

# Settings Center overlay (same layer-shell family as Search/Clipboard/Emoji).
SETTINGS_CARD_WIDTH = 780
SETTINGS_CARD_HEIGHT = 560
# Unified control center: three sibling panels, shared height, flexible center width.
CONTROL_CENTER_LEFT_WIDTH = 200
CONTROL_CENTER_RIGHT_WIDTH = 220
CONTROL_CENTER_CENTER_MIN_WIDTH = 440
CONTROL_CENTER_CENTER_SETTINGS_WIDTH = 560
CONTROL_CENTER_HEIGHT = 560
CONTROL_CENTER_GAP = 0
CONTROL_CENTER_CHROME = 24  # shell padding
CONTROL_CENTER_WIDTH = (
    CONTROL_CENTER_LEFT_WIDTH
    + CONTROL_CENTER_CENTER_MIN_WIDTH
    + CONTROL_CENTER_RIGHT_WIDTH
    + CONTROL_CENTER_CHROME
)
CONTROL_CENTER_SETTINGS_WIDTH = (
    CONTROL_CENTER_LEFT_WIDTH
    + CONTROL_CENTER_CENTER_SETTINGS_WIDTH
    + CONTROL_CENTER_RIGHT_WIDTH
    + CONTROL_CENTER_CHROME
)

# ``None`` mirrors Hyprland exactly. Set an integer to retain empty targets 1..N.
PERSISTENT_WORKSPACES: int | None = None

# Number of consecutive Hyprland workspaces represented by one draggable block.
WORKSPACES_PER_BLOCK = 3

# Layer-shell top inset. 0 anchors the bar flush with the monitor edge (y=0).
TOP_MARGIN = 0

# Retract the top bar when a floating window intrudes into its strip.
BAR_RETRACT_ENABLED = True
BAR_RETRACT_POLL_MS = 32
BAR_RETRACT_GAP_PX = 4
BAR_RETRACT_ANIM_TICK_MS = 16

# Screen-edge padding when clamping anchored popups.
POPUP_EDGE_MARGIN = 8

# Grace period before closing a popup after the pointer leaves it.
POPUP_OUTSIDE_DISMISS_GRACE_MS = 500

# Volume OSD auto-hide delay after the last default-sink volume/mute/output change.
VOLUME_OSD_HIDE_DELAY_MS = 1200

# GTK 3 exposes these through widget APIs rather than its CSS property set.
WORKSPACE_BUTTON_SPACING = 4
APPLICATION_ICON_SPACING = 6
APPLICATION_ICON_SIZE = 18
FOCUSED_APPLICATION_ICON_SIZE = 21
ACTIVE_WINDOW_ICON_SIZE = 24
ACTIVE_WINDOW_CONTENT_SPACING = 8
# How long the cava volume % stays “punched” after a change (gamefeel).
ACTIVE_WINDOW_VOLUME_FLASH_MS = 900
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

# Keyboard cat (SVG frames under shell/assets/cat/keyboard/).
# Pixel size is derived from pinned-apps / sibling height.
# Keep 0 so the glyph can sit flush on the bar floor.
KEYBOARD_CAT_VERTICAL_INSET = 0

# Tasks module.
TASKS_PATH = "tasks.json"
TASKS_ICON_SIZE = 16
TASKS_POPUP_OFFSET = 8
TASKS_POPUP_WIDTH = 380
TASKS_POPUP_MAX_HEIGHT = 520
TASKS_ROLLOVER_INTERVAL_SEC = 30
CLOCK_CALENDAR_TASKS_MAX_HEIGHT = 220

# Background task watcher (separate process; does not keep Llama loaded).
TASK_WATCHER_ENABLED = True
TASK_WATCHER_POLL_INTERVAL_SEC = 45
TASK_WATCHER_DISTRACTION_THRESHOLD_SEC = 20 * 60
TASK_WATCHER_URGENT_WINDOW_SEC = 2 * 60 * 60
TASK_WATCHER_FUTURE_HORIZON_SEC = 8 * 60 * 60
TASK_WATCHER_REMINDER_COOLDOWN_SEC = 60 * 60
TASK_WATCHER_SNOOZE_SHORT_SEC = 15 * 60
TASK_WATCHER_SNOOZE_LONG_SEC = 60 * 60
TASK_WATCHER_MAX_REMINDERS_PER_OCCURRENCE = 4
TASK_WATCHER_NOTIFICATION_TIMEOUT_MS = 12000
TASK_WATCHER_AI_ENABLED = True
TASK_WATCHER_AI_BINARY = "llama-cli"
TASK_WATCHER_AI_MODEL_PATH = "~/IA/models/llama-3.1-8b-instruct-q6_k.gguf"
TASK_WATCHER_AI_CONTEXT_SIZE = 1024
TASK_WATCHER_AI_MAX_TOKENS = 128
TASK_WATCHER_AI_TIMEOUT_SEC = 15
TASK_STARTUP_BRIEFING_ENABLED = True
TASK_STARTUP_BRIEFING_MAX_TOKENS = 120
# Mild creativity for briefing only (reminders keep llama-cli defaults).
TASK_STARTUP_BRIEFING_TEMPERATURE = 0.9
TASK_STARTUP_BRIEFING_TOP_P = 0.92
TASK_STARTUP_BRIEFING_REPEAT_PENALTY = 1.12
TASK_WATCHER_AI_NGL = 99
TASK_WATCHER_AI_BATCH_SIZE = 32
TASK_WATCHER_AI_THREADS = 2
TASK_WATCHER_AI_LAYER_COUNT = 32
TASK_WATCHER_RESOURCE_MONITOR_ENABLED = True
TASK_WATCHER_MINIMUM_VRAM_MARGIN_BYTES = 64 * 1024 * 1024
TASK_WATCHER_MAXIMUM_GPU_USAGE_PERCENT = 80.0
TASK_WATCHER_MINIMUM_AVAILABLE_RAM_BYTES = 1536 * 1024 * 1024
TASK_WATCHER_COMPUTE_OVERHEAD_BYTES = 64 * 1024 * 1024
TASK_WATCHER_QUIET_AFTER_INTERVALS = 2
TASK_WATCHER_STALE_AFTER_INTERVALS = 3

# System statistics module. Thermal thresholds live in shell/servicios/sistema/system.py.
SYSTEM_STATS_UPDATE_INTERVAL = 1
STATS_SECTION_SPACING = 12
STATS_CPU_BAR_WIDTH = 52
STATS_CPU_BAR_TEMP_MIN_C = 30.0
STATS_CPU_BAR_TEMP_MAX_C = 95.0
STATS_GPU_FAN_ICON_SIZE = 20

# System tray module.
TRAY_ICON_SIZE = 16
TRAY_ICON_PADDING = 4
TRAY_SLOT_SIZE = TRAY_ICON_SIZE + (TRAY_ICON_PADDING * 2)
TRAY_ITEM_SPACING = 4

# Network module (NetworkManager-backed service).
NETWORK_ICON_SIZE = 16
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
POWER_MENU_OFFSET = 6

# Notifications D-Bus server (org.freedesktop.Notifications).
NOTIFICATIONS_BUS_NAME = "org.freedesktop.Notifications"
NOTIFICATIONS_DEV_BUS_NAME = "org.freedesktop.Notifications.ShellDev"
NOTIFICATIONS_OBJECT_PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS_INTERFACE = "org.freedesktop.Notifications"
NOTIFICATIONS_PRODUCTION_RETRY_SEC = 5

# Bar button.
NOTIFICATION_ICON_SIZE = 16
NOTIFICATIONS_BELL_ANIMATION_MS = 650

# History persistence.
NOTIFICATIONS_HISTORY_PATH = "notifications.json"
NOTIFICATIONS_MAX_HISTORY = 200
NOTIFICATIONS_PAUSED_DEFAULT = False

# Popup list.
NOTIFICATION_POPUP_OFFSET = 10
NOTIFICATION_POPUP_WIDTH = 360
NOTIFICATION_POPUP_MAX_HEIGHT = 540
NOTIFICATION_POPUP_ICON_SIZE = 20
NOTIFICATION_POPUP_ROW_BODY_LINES = 4
NOTIFICATION_POPUP_LIST_SPACING = 8

# Stacked group window: whole-block page slide when changing parent.
# Deliberately long so rapid parent changes feel like continuous navigation.
NOTIFICATION_GROUP_SLIDE_DURATION_MS = 700

# Timeouts (milliseconds). 0 = persist until manually dismissed.
NOTIFICATIONS_DEFAULT_TIMEOUT_MS = 5000
NOTIFICATIONS_CRITICAL_TIMEOUT_MS = 15000
NOTIFICATIONS_CRITICAL_PERSIST = True

# Incoming toast (separate from history popup).
NOTIFICATIONS_TOAST_ENABLED = True
NOTIFICATIONS_MAX_VISIBLE_TOASTS = 3
NOTIFICATIONS_TOAST_STACK_STEP = 88
NOTIFICATIONS_TOAST_WIDTH = 400
NOTIFICATIONS_TOAST_MAX_HEIGHT = 120
NOTIFICATIONS_TOAST_TIMEOUT_MS = 5000

# Fullscreen "whisper": own compact HUD chip (not a scaled-down toast).
NOTIFICATIONS_WHISPER_WIDTH = 300
NOTIFICATIONS_WHISPER_MAX_HEIGHT = 34
NOTIFICATIONS_WHISPER_ICON_SIZE = 14
NOTIFICATIONS_WHISPER_MAX_VISIBLE = 1
NOTIFICATIONS_WHISPER_TIMEOUT_MS = 3200
NOTIFICATIONS_WHISPER_CRITICAL_TIMEOUT_MS = 5500
NOTIFICATIONS_WHISPER_TOP_MARGIN = 14

# Assistant card (startup briefing / Jugoo speaking). Separate from toasts.
ASSISTANT_CARD_WIDTH = 420
ASSISTANT_CARD_MAX_HEIGHT = 420
ASSISTANT_CARD_MESSAGE_MAX_HEIGHT = 300
ASSISTANT_ICON_SIZE = 32
ASSISTANT_TOP_MARGIN = 56
AI_PROMPT_TOP_MARGIN = 52
AI_PROMPT_WIDTH = 520
ASSISTANT_SLIDE_PX = 10

# History grouping: "summary" = by contacto/título (WhatsApp-style);
# "app" = todas las de una app juntas. EXCEPTIONS flips the default per app key.
NOTIFICATIONS_GROUPING_MODE = "summary"
NOTIFICATIONS_GROUPING_EXCEPTIONS = ""

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
MEDIA_POPUP_WIDTH = 320
MEDIA_POPUP_MAX_HEIGHT = 520
MEDIA_ARTWORK_SIZE = 200
MEDIA_STRAWBERRY_DESKTOP_IDS = (
    "org.strawberrymusicplayer.strawberry.desktop",
    "strawberry.desktop",
)
MEDIA_STRAWBERRY_COMMAND = "strawberry"
MEDIA_STRAWBERRY_IDLE_KILL_SEC = 60
MEDIA_STRAWBERRY_PLAY_POLL_MS = 400
MEDIA_STRAWBERRY_PLAY_POLL_MAX_MS = 15_000
MEDIA_STRAWBERRY_PLAY_RETRY_MS = 500
# Coalesce MPRIS Volume writes while dragging the popup slider.
MEDIA_VOLUME_FLUSH_MS = 40
# Strawberry volume step for global binds (MainMod+Shift+F9/F11).
MEDIA_STRAWBERRY_VOLUME_STEP = 0.05

# Pinned application dock (in-bar; overflow drops below the strip).
PINNED_APPS_VISIBLE_LIMIT = 9
PINNED_APP_ICON_SIZE = 20
PINNED_APP_SPACING = 2
PINNED_OVERFLOW_OFFSET = 4
PINNED_APPS_PATH = "pinned-apps.json"

# Application launcher overlay (Super+Space).
LAUNCHER_WIDTH = 440
LAUNCHER_MAX_HEIGHT = 520
LAUNCHER_ROW_ICON_SIZE = 28
LAUNCHER_LIST_SPACING = 2

# Clipboard picker (Super+V). History is local-only and never logged.
# Limits apply only to Jugoo persistence — never to the system clipboard.
CLIPBOARD_HISTORY_PATH = "clipboard-history.json"
CLIPBOARD_IMAGES_DIR = "clipboard/images"
CLIPBOARD_HISTORY_LIMIT = 200
CLIPBOARD_MAX_TEXT_BYTES = 1 * 1024 * 1024
CLIPBOARD_MAX_HISTORY_BYTES = 25 * 1024 * 1024
# Backward-compatible alias used by older imports/tests.
CLIPBOARD_MAX_ITEM_BYTES = CLIPBOARD_MAX_TEXT_BYTES
CLIPBOARD_PREVIEW_CHARS = 96
CLIPBOARD_PREVIEW_LINES = 2
CLIPBOARD_THUMBNAIL_SIZE = 56
# Split card: history list + full detail pane (same window).
CLIPBOARD_PICKER_WIDTH = 820
CLIPBOARD_PICKER_HEIGHT = 480
CLIPBOARD_LIST_WIDTH = 300
CLIPBOARD_DETAIL_IMAGE_MAX = 520

# Emoji picker (Super+period).
EMOJI_PICKER_COLUMNS = 9

# Audio visualizer (PipeWire monitor sampling for the active-window block).
AUDIO_VISUALIZER_BAR_COUNT = 14
AUDIO_VISUALIZER_FPS = 14
AUDIO_VISUALIZER_INTERVAL_MS = 1000 // AUDIO_VISUALIZER_FPS
AUDIO_VISUALIZER_PCM_RATE = 16000
AUDIO_VISUALIZER_PCM_CHANNELS = 1
AUDIO_VISUALIZER_PCM_LATENCY = 256
