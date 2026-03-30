-- atomic/inventory_remove.lua
-- Atom: Remove items from character's inventory.
-- Args: {"name": "iron-plate", "count": 50}
-- Returns: {"removed": N}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local inv = agent.get_inventory()
    if not inv then
        return serialize({error = "no inventory"})
    end

    local name = args_str:match('"name"%s*:%s*"([^"]+)"')
    local count = args_str:match('"count"%s*:%s*([%d]+)')

    if not name then
        return serialize({error = "missing item name"})
    end

    local n = tonumber(count) or 1
    local removed = inv.remove{name = name, count = n}

    return serialize({
        removed = removed,
        item = name,
    })
end