#!/usr/bin/env bash
# Quita Hyprland de xsessions (rompe LightDM) y deja solo la sesión Wayland.
set -euo pipefail

if [[ -f /usr/share/xsessions/hyprland.desktop ]]; then
  sudo rm -f /usr/share/xsessions/hyprland.desktop
  echo "Eliminado /usr/share/xsessions/hyprland.desktop"
else
  echo "Ya no estaba en xsessions (bien)."
fi

# Restaurar sesión por defecto a XFCE para no rebotar al login
cat > "$HOME/.dmrc" <<'EOF'
[Desktop]
Session=xfce
EOF
echo "Sesión por defecto del login: XFCE (puedes entrar normal)."
echo
echo "Para entrar a Hyprland usa el metodo TTY (mas fiable con LightDM):"
echo "  1) Cierra sesion de XFCE"
echo "  2) En la pantalla negra de login: Ctrl+Alt+F3"
echo "  3) Usuario + contraseña"
echo "  4) Escribe: start-hyprland"
echo "  5) Se abrira una terminal con los atajos"
echo
echo "Para volver a XFCE: Super+Shift+E  (sale de Hyprland)"
echo "luego: Ctrl+Alt+F7  o escribe: exit  y vuelve al login F7"
