-- control.lua
-- RCON command registration and script dispatch.
-- All agent interaction goes through the single /agent command.
--
-- Script hierarchy:
--   atomic/  - Raw API calls (single operation)
--   actions/ - Common workflows (encapsulated for convenience)
--   examples/- Example scripts (for agent to learn from)

local serialize = require("scripts.lib.serialize")

-- Pre-load all scripts (static, loaded at startup)
local scripts = {}

-- Script cache for dynamic loading (hot-reload support)
-- Stores loaded script functions by name
local script_cache = {}

-- Dynamic script registry: stores code strings for runtime loading
-- This allows hot-reload by sending code via RCON instead of using loadfile
local dynamic_scripts = {}

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

-- Helper: load script with caching (for hot-reload support)
-- Note: Factorio sandbox blocks loadfile/require at runtime.
-- Instead, we use load() on code strings stored in dynamic_scripts.
-- We wrap the loaded code to provide serialize in the closure.
local serialize_fn = serialize  -- Capture at startup

local function load_script(name)
    -- Check cache first
    if script_cache[name] then
        return script_cache[name]
    end

    -- Check dynamic scripts (code strings registered via RCON)
    if dynamic_scripts[name] then
        -- Wrap code with serialize injection
        -- The code expects serialize to be available, so we inject it
        local wrapped_code = "local serialize = ...; " .. dynamic_scripts[name]
        local ok, fn = pcall(load, wrapped_code)
        if ok and fn then
            -- Call with serialize as argument, returns the actual script function
            local script_fn = fn(serialize_fn)
            if script_fn then
                script_cache[name] = script_fn
                return script_fn
            end
        end
    end

    -- Fall back to pre-loaded scripts
    return nil
end

-- Helper: register a dynamic script (code string)
local function register_script(name, code)
    dynamic_scripts[name] = code
    script_cache[name] = nil  -- Clear cache to force reload
    return {ok = true, registered = name}
end

-- Helper: clear script cache for reload
local function reload_script(name)
    if name then
        -- Clear specific script
        script_cache[name] = nil
        return {ok = true, reloaded = name}
    else
        -- Clear all
        script_cache = {}
        return {ok = true, reloaded = "all"}
    end
end

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

    -- Handle reload commands
    if script_name == "reload" then
        local target = args_str:match("^%s*(%S+)")
        local result = reload_script(target)
        rcon.print(serialize(result))
        return
    end

    if script_name == "reload_all" then
        local result = reload_script(nil)
        rcon.print(serialize(result))
        return
    end

    -- Handle register command (for hot-reload)
    -- Format: /agent register atomic.my_script <<<code>>>
    -- The code is wrapped in <<< >>> markers for simple parsing
    if script_name == "register" then
        -- Parse: register name <<<code>>>
        local target, code_str = args_str:match("^%s*(%S+)%s*<<<(.*)>>>$")
        if target and code_str then
            local result = register_script(target, code_str)
            rcon.print(serialize(result))
            return
        else
            rcon.print(serialize({error = "register format: name <<<code>>>"}))
            return
        end
    end

    -- Try cache first, then fall back to pre-loaded scripts
    local script_fn = load_script(script_name)
    if not script_fn then
        script_fn = scripts[script_name]
    end

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