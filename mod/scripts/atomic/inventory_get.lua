-- atomic/inventory_get.lua
-- Atom: Get character's main inventory contents.
-- Args: (none)
-- Returns: {"items": [...], "free_slots": N}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local inv = agent.get_inventory()
    if not inv then
        return serialize({error = "no inventory"})
    end

    local items = {}
    for _, item in pairs(inv.get_contents()) do
        items[#items + 1] = {
            name = item.name,
            count = item.count,
            quality = item.quality,
        }
    end

    return serialize({
        items = items,
        free_slots = inv.count_empty_stacks(),
    })
end