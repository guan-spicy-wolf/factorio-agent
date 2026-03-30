-- atomic/cursor_set.lua
-- Atom: Set cursor stack to an item.
-- NOTE: Works on character entity in headless mode!
-- Args: {"name": "iron-chest"}
-- Returns: {"set": true} or {"error": "..."}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local cursor = e.cursor_stack
    if not cursor then
        return serialize({error = "no cursor_stack on character"})
    end

    local name = args_str:match('"name"%s*:%s*"([^"]+)"')
    if not name then
        return serialize({error = "missing item name"})
    end

    if not game.item_prototypes[name] then
        return serialize({error = "unknown item: " .. name})
    end

    local success = cursor.set_stack{name = name, count = 1}

    if success then
        return serialize({set = true, item = name})
    else
        return serialize({error = "failed to set cursor"})
    end
end