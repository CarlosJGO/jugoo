#!/bin/sh
# LightDM / greeter entry for Hyprland (Wayland).
# Referenced by /usr/share/wayland-sessions/hyprland.desktop

set -eu

export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_DESKTOP=Hyprland
export XDG_SESSION_TYPE=wayland
export XDG_DATA_DIRS="/usr/local/share:/usr/share:/var/lib/flatpak/exports/share:${HOME}/.local/share/flatpak/exports/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export XDG_CONFIG_DIRS="/etc/xdg${XDG_CONFIG_DIRS:+:$XDG_CONFIG_DIRS}"
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"

# Prefer the XDG config under ~/.config/hypr (same content as this tree when hardlinked).
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

TIMEOUT_SEC=5
INTERVAL_SEC=0.1
vt="${XDG_VTNR:-}"

if [ -n "$vt" ] && [ -r /sys/class/tty/tty0/active ]; then
    deadline=$(( $(date +%s) + TIMEOUT_SEC ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        active="$(cat /sys/class/tty/tty0/active 2>/dev/null || true)"
        if [ "$active" = "tty${vt}" ]; then
            break
        fi
        sleep "$INTERVAL_SEC"
    done
fi

if [ ! -x /usr/bin/start-hyprland ]; then
    echo "start-hyprland: no encontrado" >&2
    exit 1
fi

exec /usr/bin/start-hyprland "$@"
