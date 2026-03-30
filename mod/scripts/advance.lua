-- advance.lua
-- Advance game time by N ticks using game.ticks_to_run.
-- Args: plain number of ticks (default 60 = 1 second at normal speed).
-- Returns: {"ticks_to_run": N, "tick_before": T}

local serialize = require("scripts.lib.serialize")

return function(args_str)
    local n = tonumber(args_str) or 60

    if n < 1 then
        return serialize({error = "ticks must be >= 1"})
    end

    local tick_before = game.tick
    game.ticks_to_run = n

    return serialize({
        ticks_to_run = n,
        tick_before = tick_before,
    })
end
