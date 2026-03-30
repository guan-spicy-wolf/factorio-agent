-- atomic/teleport.lua
-- Atom: Teleport character to a position instantly.
-- Args: {"x": 10, "y": 5}
-- Returns: {"teleported": true, "position": {...}} or {"error": "..."}

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
    local success = e.teleport(target)

    if success then
        return serialize({
            teleported = true,
            position = {x = e.position.x, y = e.position.y},
        })
    else
        return serialize({error = "teleport failed"})
    end
end