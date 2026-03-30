-- atomic/cursor_clear.lua
-- Atom: Clear cursor stack.
-- Args: (none)
-- Returns: {"cleared": true} or {"error": "..."}

local serialize = require("scripts.lib.serialize")

return function(args_str)
    local player = game.players[1]
    if not player or not player.valid then
        return serialize({error = "no player"})
    end

    local cursor = player.cursor_stack
    if not cursor then
        return serialize({cleared = true})  -- Already empty
    end

    cursor.clear()

    return serialize({cleared = true})
end