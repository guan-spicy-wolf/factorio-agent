-- ping.lua
-- Minimal verification script. Returns game tick and mod info.
local serialize = require("scripts.lib.serialize")

return function(args)
    return serialize({
        tick = game.tick,
        mod = "factorio-agent",
        status = "ok",
    })
end
