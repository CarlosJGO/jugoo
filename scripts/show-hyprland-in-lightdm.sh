#!/usr/bin/env bash
# Make Hyprland appear in LightDM's session menu (XFCE greeter).
# LightDM often hides Wayland-only sessions; copying into xsessions fixes that.
set -euo pipefail

sudo cp /usr/share/wayland-sessions/hyprland.desktop /usr/share/xsessions/hyprland.desktop
sudo tee /etc/lightdm/lightdm-gtk-greeter.conf.d/90-show-session.conf >/dev/null <<'EOF'
[greeter]
indicators=~host;~spacer;~clock;~spacer;~session;~language;~a11y;~power
EOF

echo "OK. Ahora cierra sesión (no hace falta apagar)."
echo "En la pantalla de login, arriba a la derecha (o cerca del campo de contraseña)"
echo "haz clic en el icono de sesión y elige: Hyprland"
echo "Luego escribe tu contraseña y Enter."
