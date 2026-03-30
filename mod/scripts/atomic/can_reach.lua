-- atomic/can_reach.lua
-- Atom: Check if character can reach a position or entity.
-- Args: {"x": 10, "y": 5}
-- Returns: {"can_reach": true/false, "distance": N}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
    local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')

    if not x or not y then
        return serialize({error = "missing x or y"})
    end

    local target = {x = tonumber(x), y = tonumber(y)}
    local agent_pos = e.position

    local dist = agent.distance(agent_pos, target)
    local reach = e.reach_distance or 10

    return serialize({
        can_reach = dist <= reach,
        distance = math.floor(dist * 10) / 10,
        reach_distance = reach,
    })
end