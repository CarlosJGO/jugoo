-- Managed by Jugoo acomodador.py. Prefer editing the copy under packaging/hyprland/.
-- Keybinds that talk to the running Jugoo instance (Gio.Application primary).
-- See: jugoo action list

local mainMod = "SUPER"
local jugoo = "${XDG_BIN_HOME:-$HOME/.local/bin}/jugoo"

local function jugoo_cmd(args)
    return hl.dsp.exec_cmd("/bin/sh -c '\"" .. jugoo .. "\" " .. args .. "'")
end

hl.bind(mainMod .. " + Return", jugoo_cmd("action ask"))
hl.bind(mainMod .. " + Space", jugoo_cmd("action launcher"))
hl.bind(mainMod .. " + period", jugoo_cmd("action emoji"))
hl.bind(mainMod .. " + V", jugoo_cmd("action clipboard"))
hl.bind(mainMod .. " + Z", jugoo_cmd("action settings"))
hl.bind(mainMod .. " + X", jugoo_cmd("action control-center"))
hl.bind(mainMod .. " + A", jugoo_cmd("action notifications"))
hl.bind(mainMod .. " + ALT + C", jugoo_cmd("action session"))

hl.bind(mainMod .. " + F10", jugoo_cmd("action playStopMusic"), { locked = true })
hl.bind(mainMod .. " + SHIFT + F10", jugoo_cmd("action media"), { locked = true })
hl.bind(
    mainMod .. " + SHIFT + F9",
    jugoo_cmd("action musicVolumeDown"),
    { locked = true, repeating = true }
)
hl.bind(
    mainMod .. " + SHIFT + F11",
    jugoo_cmd("action musicVolumeUp"),
    { locked = true, repeating = true }
)
