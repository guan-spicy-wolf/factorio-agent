-- control.lua
-- RCON command registration, script dispatch, and time control.
-- All agent interaction goes through the single /agent command.

local serialize = require("scripts.lib.serialize")

-- Pre-load all scripts into dispatch table.
-- require() cannot be called inside command handlers.
local scripts = {
    ping    = require("scripts.ping"),
    inspect = require("scripts.inspect"),
    advance = require("scripts.advance"),
}

-- Time control: pause ticks on init so the game world is frozen
-- until the agent explicitly advances time via advance.lua.
script.on_init(function()
    game.tick_paused = true
end)

-- Register the single /agent RCON entry point.
-- Usage: /agent <script_name> [args...]
commands.add_command("agent", "Execute an agent script", function(command)
    local param = command.parameter
    if not param or param == "" then
        rcon.print(serialize({error = "usage: /agent <script_name> [args]"}))
        return
    end

    -- Split: first word = script name, rest = arguments string
    local script_name, args_str = param:match("^(%S+)%s*(.*)")
    if not script_name then
        rcon.print(serialize({error = "failed to parse command"}))
        return
    end

    local script_fn = scripts[script_name]
    if not script_fn then
        rcon.print(serialize({error = "unknown script: " .. script_name}))
        return
    end

    -- Execute with pcall for error isolation
    local ok, result = pcall(script_fn, args_str)
    if ok then
        rcon.print(result or serialize({ok = true}))
    else
        rcon.print(serialize({error = tostring(result)}))
    end
end)
