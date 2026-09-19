-- Managed by Jugoo acomodador.py. Prefer editing the copy under packaging/hyprland/.
-- Window / layer rules for com.jugoo.Shell surfaces (float popups, no focus fights).

-- Generic Jugoo floating surfaces positioned by the shell.
hl.window_rule({
    name = "shell-popups",
    match = { title = "^Jugoo " },
    float = true,
    center = false,
    persistent_size = false,
    no_focus = true,
    no_initial_focus = true,
    focus_on_activate = false,
    suppress_event = "activate activatefocus",
})

hl.window_rule({
    name = "NO-FOCUS-SHELL-NOTIFICATIONS",
    match = {
        class = "^com\\.jugoo\\.Shell$",
        title = "^Jugoo Notification Toast",
    },
    no_focus = true,
    no_initial_focus = true,
    focus_on_activate = false,
    suppress_event = "activate activatefocus",
})

hl.window_rule({
    name = "SHELL-NOTIFICATION-GROUP",
    match = {
        class = "^com\\.jugoo\\.Shell$",
        title = "^Jugoo Notification Group$",
    },
    float = true,
    center = false,
    persistent_size = false,
    no_focus = false,
    no_initial_focus = false,
    focus_on_activate = true,
})

local interactive = {
    { "SHELL-APP-LAUNCHER", "^Jugoo Launcher$" },
    { "SHELL-CLIPBOARD-PICKER", "^Jugoo Clipboard$" },
    { "SHELL-EMOJI-PICKER", "^Jugoo Emoji$" },
    { "SHELL-SETTINGS", "^Jugoo Configuraciones$" },
    { "SHELL-TASKS", "^Jugoo Tasks$" },
}

for _, entry in ipairs(interactive) do
    hl.window_rule({
        name = entry[1],
        match = { title = entry[2] },
        float = true,
        center = false,
        persistent_size = false,
        no_focus = false,
        no_initial_focus = false,
        focus_on_activate = true,
    })
end

-- Door animations live in GTK; suppress Hyprland layer slide.
hl.layer_rule({
    name = "jugoo-puertas-no-anim",
    match = {
        namespace = "^shell-(app-launcher|clipboard-picker|emoji-picker)$",
    },
    no_anim = true,
})
