-- control.lua
-- RCON command registration and script dispatch.
-- All agent interaction goes through the single /agent command.
--
-- Script hierarchy:
--   atomic/  - Raw API calls (single operation)
--   actions/ - Common workflows (encapsulated for convenience)
--   examples/- Example scripts (for agent to learn from)

local serialize = require("scripts.lib.serialize")

-- Pre-load all scripts
local scripts = {}

-- Atomic operations (raw API)
for _, name in ipairs({
    "teleport", "inventory_get", "inventory_add", "inventory_remove",
    "inventory_count", "cursor_set", "cursor_clear", "cursor_get", "cursor_test",
    "build_from_cursor", "mine_entity", "can_reach", "can_place",
}) do
    scripts["atomic." .. name] = require("scripts.atomic." .. name)
end

-- Action scripts (encapsulated workflows)
for _, name in ipairs({
    "spawn", "move", "inventory", "place", "remove", "inspect",
}) do
    scripts["actions." .. name] = require("scripts.actions." .. name)
end

-- Example scripts (for learning)
for _, name in ipairs({
    "build_belt_line", "setup_mining",
}) do
    scripts["examples." .. name] = require("scripts.examples." .. name)
end

-- Legacy shortcuts (map old names to actions)
scripts["spawn"] = scripts["actions.spawn"]
scripts["move"] = scripts["actions.move"]
scripts["inventory"] = scripts["actions.inventory"]
scripts["place"] = scripts["actions.place"]
scripts["remove"] = scripts["actions.remove"]
scripts["inspect"] = scripts["actions.inspect"]

-- Utility scripts
scripts["ping"] = require("scripts.ping")
scripts["advance"] = require("scripts.advance")

-- Time control: pause ticks on init
script.on_init(function()
    game.tick_paused = true
    storage.agent = nil
end)

-- Helper: list available scripts
local function list_scripts()
    local list = {
        atomic = {},
        actions = {},
        examples = {},
    }
    for name, _ in pairs(scripts) do
        if name:match("^atomic%.") then
            list.atomic[#list.atomic + 1] = name:sub(8)
        elseif name:match("^actions%.") then
            list.actions[#list.actions + 1] = name:sub(9)
        elseif name:match("^examples%.") then
            list.examples[#list.examples + 1] = name:sub(10)
        end
    end
    table.sort(list.atomic)
    table.sort(list.actions)
    table.sort(list.examples)
    return list
end

-- Register /agent command
commands.add_command("agent", "Execute an agent script", function(command)
    local param = command.parameter
    if not param or param == "" then
        rcon.print(serialize({
            usage = "/agent <script_name> [args]",
            scripts = list_scripts(),
        }))
        return
    end

    -- Parse script name and args
    local script_name, args_str = param:match("^(%S+)%s*(.*)")
    if not script_name then
        rcon.print(serialize({error = "failed to parse command"}))
        return
    end

    local script_fn = scripts[script_name]
    if not script_fn then
        -- Suggest similar scripts
        local suggestions = {}
        for name, _ in pairs(scripts) do
            if name:find(script_name, 1, true) then
                suggestions[#suggestions + 1] = name
            end
        end
        rcon.print(serialize({
            error = "unknown script: " .. script_name,
            suggestions = #suggestions > 0 and suggestions or nil,
        }))
        return
    end

    -- Execute with error handling
    local ok, result = pcall(script_fn, args_str)
    if ok then
        rcon.print(result or serialize({ok = true}))
    else
        rcon.print(serialize({error = tostring(result)}))
    end
end)