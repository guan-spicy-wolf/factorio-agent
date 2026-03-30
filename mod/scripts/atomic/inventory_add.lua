-- atomic/inventory_add.lua
-- Atom: Insert items into character's inventory.
-- Args: {"name": "iron-plate", "count": 100}
-- Returns: {"inserted": N}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local name = args_str:match('"name"%s*:%s*"([^"]+)"')
    local count = args_str:match('"count"%s*:%s*([%d]+)')

    if not name then
        return serialize({error = "missing item name"})
    end

    local n = tonumber(count) or 1
    local inserted = e.insert{name = name, count = n}

    return serialize({
        inserted = inserted,
        item = name,
    })
end