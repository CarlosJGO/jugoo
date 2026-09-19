-- Managed by Jugoo acomodador.py. Prefer editing the copy under packaging/hyprland/.
-- Session autostart pieces owned by Jugoo (safe to combine with your own autostart).

hl.on("hyprland.start", function()
    hl.exec_cmd("systemctl --user start hyprpolkitagent.service")
    hl.exec_cmd("/bin/sh -c 'exec \"${XDG_BIN_HOME:-$HOME/.local/bin}/jugoo\"'")
end)
