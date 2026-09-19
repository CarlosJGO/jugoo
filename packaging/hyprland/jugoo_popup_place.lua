-- Posición de spawn para popups flotantes de Jugoo.
-- Jugoo escribe $XDG_RUNTIME_DIR/jugoo/popup-spawn.json antes del map.
-- Este script instala una windowrule `move` estática para que windowsIn
-- arranque ya en esas coordenadas. No usa hl.dsp.window.move (eso anima windowsMove).
-- Solo actúa si el título está en el JSON; Notifications no publican nada.

local SPAWN_FILE = (os.getenv("XDG_RUNTIME_DIR") or "/tmp") .. "/jugoo/popup-spawn.json"

local function json_string(value)
    return '"' .. tostring(value):gsub("\\", "\\\\"):gsub('"', '\\"') .. '"'
end

local function re2_escape(text)
    return (text:gsub("([%.%^%$%*%+%-%?%(%)%[%]%{%}\\|])", "\\%1"))
end

local function rule_name(title)
    return "jugoo-spawn-" .. title:gsub("[^%w]+", "-")
end

local function load_spawns()
    local file = io.open(SPAWN_FILE, "r")
    if not file then
        return {}
    end
    local body = file:read("*a") or ""
    file:close()

    local spawns = {}
    for title, rest in body:gmatch('"([^"]+)":%s*{([^}]+)}') do
        local x = tonumber(rest:match('"x"%s*:%s*(-?%d+)'))
        local y = tonumber(rest:match('"y"%s*:%s*(-?%d+)'))
        local local_x = tonumber(rest:match('"local_x"%s*:%s*(-?%d+)'))
        local local_y = tonumber(rest:match('"local_y"%s*:%s*(-?%d+)'))
        local monitor = rest:match('"monitor"%s*:%s*"([^"]*)"')
        if x and y then
            spawns[title] = {
                x = x,
                y = y,
                local_x = local_x or x,
                local_y = local_y or y,
                monitor = monitor or "",
            }
        end
    end
    return spawns
end

local function save_spawns(spawns)
    local parts = {}
    for title, pos in pairs(spawns) do
        local monitor = "null"
        if pos.monitor and pos.monitor ~= "" then
            monitor = json_string(pos.monitor)
        end
        parts[#parts + 1] = string.format(
            "%s:{\"x\":%d,\"y\":%d,\"local_x\":%d,\"local_y\":%d,\"monitor\":%s}",
            json_string(title),
            pos.x,
            pos.y,
            pos.local_x,
            pos.local_y,
            monitor
        )
    end
    local file = io.open(SPAWN_FILE, "w")
    if not file then
        return
    end
    file:write("{\n  " .. table.concat(parts, ",\n  ") .. "\n}\n")
    file:close()
end

local function apply_spawn_rule(title, pos)
    local rule = {
        name = rule_name(title),
        match = { title = "^" .. re2_escape(title) .. "$" },
        move = { pos.local_x, pos.local_y },
    }
    if pos.monitor and pos.monitor ~= "" then
        rule.monitor = pos.monitor
    end
    hl.window_rule(rule)
end

function jugoo_reload_popup_spawns()
    for title, pos in pairs(load_spawns()) do
        apply_spawn_rule(title, pos)
    end
end

hl.on("window.open_early", function(window)
    if not window then
        return
    end
    local title = window.title or ""
    if title == "" then
        return
    end

    local spawns = load_spawns()
    local pos = spawns[title]
    if not pos then
        return
    end

    apply_spawn_rule(title, pos)
    spawns[title] = nil
    save_spawns(spawns)
end)
