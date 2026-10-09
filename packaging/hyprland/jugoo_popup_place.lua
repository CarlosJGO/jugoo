-- Posición de spawn para popups flotantes de Jugoo.
-- Jugoo escribe $XDG_RUNTIME_DIR/jugoo/popup-spawn.json antes del map.
-- La ventana es un toplevel normal: nace ya en su sitio (centrada en el bloque
-- y por debajo de la barra). La entrada y la salida las anima Hyprland.

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
        local union_local_x = tonumber(rest:match('"union_local_x"%s*:%s*(-?%d+)'))
        local union_local_y = tonumber(rest:match('"union_local_y"%s*:%s*(-?%d+)'))
        local union_w = tonumber(rest:match('"union_w"%s*:%s*(-?%d+)'))
        local union_h = tonumber(rest:match('"union_h"%s*:%s*(-?%d+)'))
        local origin_local_x = tonumber(rest:match('"origin_local_x"%s*:%s*(-?%d+)'))
        local origin_local_y = tonumber(rest:match('"origin_local_y"%s*:%s*(-?%d+)'))
        local origin_w = tonumber(rest:match('"origin_w"%s*:%s*(-?%d+)'))
        local origin_h = tonumber(rest:match('"origin_h"%s*:%s*(-?%d+)'))
        if x and y then
            spawns[title] = {
                x = x,
                y = y,
                local_x = local_x or x,
                local_y = local_y or y,
                monitor = monitor or "",
                union_local_x = union_local_x,
                union_local_y = union_local_y,
                union_w = union_w or 0,
                union_h = union_h or 0,
                origin_local_x = origin_local_x,
                origin_local_y = origin_local_y,
                origin_w = origin_w or 0,
                origin_h = origin_h or 0,
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
            "%s:{\"x\":%d,\"y\":%d,\"local_x\":%d,\"local_y\":%d,\"monitor\":%s,\"union_local_x\":%d,\"union_local_y\":%d,\"union_w\":%d,\"union_h\":%d,\"origin_local_x\":%d,\"origin_local_y\":%d,\"origin_w\":%d,\"origin_h\":%d}",
            json_string(title),
            pos.x,
            pos.y,
            pos.local_x,
            pos.local_y,
            monitor,
            pos.union_local_x or pos.local_x,
            pos.union_local_y or pos.local_y,
            pos.union_w or 0,
            pos.union_h or 0,
            pos.origin_local_x or pos.local_x,
            pos.origin_local_y or pos.local_y,
            pos.origin_w or 0,
            pos.origin_h or 0
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
    -- Entrada y salida del compositor. No se apaga la animación ni el borde:
    -- la ventana sigue siendo un toplevel normal.
    if (pos.origin_w or 0) >= 8 and (pos.origin_h or 0) >= 8 then
        rule.animation = "popin 70%"
    end
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
