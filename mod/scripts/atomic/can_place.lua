-- atomic/can_place.lua
-- Atom: Check if an entity can be placed at position.
-- Args: {"name": "iron-chest", "x": 10, "y": 5, "direction": 2}
-- Returns: {"can_place": true/false}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

local DIR_MAP = {
    [0] = defines.direction.north,
    [1] = defines.direction.east,
    [2] = defines.direction.south,
    [3] = defines.direction.west,
}

return function(args_str)
    local surface = agent.get_surface()
    local force = agent.get_force()

    if not surface then
        return serialize({error = "agent not spawned"})
    end

    local name = args_str:match('"name"%s*:%s*"([^"]+)"')
    local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
    local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')
    local dir = args_str:match('"direction"%s*:%s*([%d]+)')

    if not name or not x or not y then
        return serialize({error = "missing name, x, or y"})
    end

    local position = {x = tonumber(x), y = tonumber(y)}
    local direction = DIR_MAP[tonumber(dir) or 0] or defines.direction.north

    local can_place = surface.can_place_entity{
        name = name,
        position = position,
        direction = direction,
        force = force,
    }

    return serialize({
        can_place = can_place,
        name = name,
        position = position,
    })
end