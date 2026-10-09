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

-- Grouped-notifications card: kept under the notifications popup. A border or
-- shadow would show around the popup while the card rests behind it. No
-- animation by default (mapping and sinking it behind the popup are instant);
-- Jugoo lifts no_anim per move so Hyprland slides it out and back with
-- windowsMove.
hl.window_rule({
    name = "SHELL-NOTIFICATION-DECK",
    match = {
        class = "^com\\.jugoo\\.Shell$",
        title = "^Jugoo Notification Deck$",
    },
    float = true,
    center = false,
    persistent_size = false,
    -- Focusable so the pointer (and the scroll wheel) stays on it instead of
    -- going to the window behind; it still never takes focus by itself.
    no_focus = false,
    no_initial_focus = true,
    -- Hovering it keeps keyboard focus on the popup: losing it would swap the
    -- popup's border to the inactive colours on every crossing.
    no_follow_mouse = true,
    focus_on_activate = false,
    suppress_event = "activate activatefocus",
    border_size = 0,
    no_shadow = true,
    no_anim = true,
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

-- Door and edge-slide animations live in GTK; suppress Hyprland layer slide.
hl.layer_rule({
    name = "jugoo-puertas-no-anim",
    match = {
        namespace = "^shell-(app-launcher|clipboard-picker|emoji-picker|steam-games)$",
    },
    no_anim = true,
})
