-- atomic/inventory_count.lua
-- Atom: Count items in character's inventory.
-- Args: {"name": "iron-plate"}
-- Returns: {"count": N}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local inv = agent.get_inventory()
    if not inv then
        return serialize({error = "no inventory"})
    end

    local name = args_str:match('"name"%s*:%s*"([^"]+)"')

    if not name then
        return serialize({error = "missing item name"})
    end

    local count = inv.get_item_count(name)

    return serialize({
        item = name,
        count = count,
    })
end