-- atomic/cursor_get.lua
-- Atom: Get current cursor stack contents.
-- Args: (none)
-- Returns: {"item": "...", "count": N} or {"empty": true}

local serialize = require("scripts.lib.serialize")

return function(args_str)
    local player = game.players[1]
    if not player or not player.valid then
        return serialize({error = "no player"})
    end

    local cursor = player.cursor_stack
    if not cursor or cursor.is_empty() then
        return serialize({empty = true})
    end

    return serialize({
        item = cursor.name,
        count = cursor.count,
        valid = cursor.valid,
    })
end