#!/usr/bin/env bash
# Bootstrap Jugoo deps + Hyprland on Debian 13 (Trixie).
# Run from a real terminal so sudo can ask for your password:
#   bash scripts/bootstrap-debian.sh
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run as your normal user (the script will sudo when needed)." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKPORTS_FILE=/etc/apt/sources.list.d/trixie-backports.sources

echo "==> Enabling trixie-backports (if missing)"
if [[ ! -f "${BACKPORTS_FILE}" ]]; then
  sudo tee "${BACKPORTS_FILE}" >/dev/null <<'EOF'
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie-backports
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
fi

echo "==> Updating apt"
sudo apt-get update

echo "==> Installing Jugoo runtime packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  gir1.2-gtklayershell-0.1 \
  gir1.2-dbusmenu-gtk3-0.4 \
  wl-clipboard \
  wtype \
  pipewire-bin \
  xdg-desktop-portal \
  xdg-desktop-portal-gtk \
  swaybg

echo "==> Installing Hyprland from backports"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -t trixie-backports \
  hyprland \
  xdg-desktop-portal-hyprland \
  hyprland-guiutils || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -t trixie-backports hyprland

echo "==> Optional: keyboard input group for keyboard-cat widget"
if ! id -nG "${USER}" | tr ' ' '\n' | grep -qx input; then
  sudo usermod -aG input "${USER}"
  echo "Added ${USER} to group 'input'. Log out/in for it to apply."
fi

echo "==> Refreshing Jugoo XDG identity"
cd "${ROOT}"
python3 -m shell --install

echo
echo "Done."
echo "1) Log out of XFCE."
echo "2) At the display manager, choose the Hyprland session."
echo "3) Inside Hyprland, run: jugoo"
echo "   (or rely on exec-once in ~/.config/hypr/hyprland.conf)"
echo
echo "Verify GI bindings:"
echo "  python3 -c \"import gi; gi.require_version('GtkLayerShell','0.1'); print('OK')\""
