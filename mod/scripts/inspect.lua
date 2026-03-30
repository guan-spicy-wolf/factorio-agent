-- inspect.lua
-- Query entities and resources in a specified area.
-- Args (JSON): {"x": 0, "y": 0, "radius": 10} or plain number for radius around origin.
-- Returns: {"entities": [...], "count": N, "tick": T}

local serialize = require("scripts.lib.serialize")

local MAX_ENTITIES = 50  -- limit to avoid RCON truncation

return function(args_str)
    local surface = game.surfaces["nauvis"]
    local x, y, radius = 0, 0, 10

    -- Parse args: either a plain number (radius) or JSON-like
    local num = tonumber(args_str)
    if num then
        radius = num
    elseif args_str and args_str ~= "" then
        -- Simple key-value parsing for x, y, radius
        local px = args_str:match('"x"%s*:%s*([%-%.%d]+)')
        local py = args_str:match('"y"%s*:%s*([%-%.%d]+)')
        local pr = args_str:match('"radius"%s*:%s*([%-%.%d]+)')
        if px then x = tonumber(px) or 0 end
        if py then y = tonumber(py) or 0 end
        if pr then radius = tonumber(pr) or 10 end
    end

    local area = {{x - radius, y - radius}, {x + radius, y + radius}}
    local entities = surface.find_entities_filtered{area = area}

    local result = {}
    local count = math.min(#entities, MAX_ENTITIES)
    for i = 1, count do
        local e = entities[i]
        result[i] = {
            name = e.name,
            type = e.type,
            position = {x = e.position.x, y = e.position.y},
        }
    end

    return serialize({
        entities = result,
        count = count,
        total = #entities,
        tick = game.tick,
    })
end
