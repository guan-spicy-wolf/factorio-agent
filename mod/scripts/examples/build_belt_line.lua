-- examples/build_belt_line.lua
-- Example: Build a line of transport belts.
-- Demonstrates: encapsulating a reusable workflow.
--
-- Usage: /agent examples.build_belt_line {"start_x": 0, "start_y": 0, "length": 10, "direction": 1}
--   direction: 0=north, 1=east, 2=south, 3=west
--
-- This script shows how to:
--   1. Check inventory for required items
--   2. Loop over positions
--   3. Place multiple entities
--   4. Handle errors gracefully

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

local DIR_DELTA = {
    [0] = {dx = 0, dy = -1},  -- north
    [1] = {dx = 1, dy = 0},   -- east
    [2] = {dx = 0, dy = 1},   -- south
    [3] = {dx = -1, dy = 0},  -- west
}

local REACH_DISTANCE = 10

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local player = game.players[1]
    if not player or not player.valid then
        return serialize({error = "no player"})
    end

    -- Parse args
    local start_x = args_str:match('"start_x"%s*:%s*([%-%.%d]+)') or "0"
    local start_y = args_str:match('"start_y"%s*:%s*([%-%.%d]+)') or "0"
    local length = args_str:match('"length"%s*:%s*([%d]+)') or "5"
    local dir = args_str:match('"direction"%s*:%s*([%d]+)') or "1"

    local x = tonumber(start_x)
    local y = tonumber(start_y)
    local n = tonumber(length)
    local direction = tonumber(dir)

    -- Validate
    if n > 50 then
        return serialize({error = "length too long (max 50)"})
    end

    local delta = DIR_DELTA[direction]
    if not delta then
        return serialize({error = "invalid direction (0-3)"})
    end

    -- Check inventory
    local inv = agent.get_inventory()
    local have = inv.get_item_count("transport-belt")
    if have < n then
        return serialize({
            error = "not enough transport-belt",
            have = have,
            need = n,
        })
    end

    -- Build belt line
    local placed = {}
    local errors = {}

    for i = 0, n - 1 do
        local pos = {x = x + delta.dx * i, y = y + delta.dy * i}

        -- Move if needed
        if agent.distance(e.position, pos) > REACH_DISTANCE then
            e.teleport({x = pos.x - 2, y = pos.y - 2})
        end

        -- Remove from inventory
        inv.remove{name = "transport-belt", count = 1}

        -- Set cursor and build
        local cursor = player.cursor_stack
        cursor.set_stack{name = "transport-belt", count = 1}

        local success = pcall(function()
            player.build_from_cursor{
                position = pos,
                direction = direction,
                build_mode = defines.build_mode.real,
            }
        end)

        -- Clear cursor
        if not cursor.is_empty() then
            -- Failed, return item
            inv.insert{name = "transport-belt", count = cursor.count}
            cursor.clear()
            errors[#errors + 1] = {position = pos, reason = "collision"}
        else
            placed[#placed + 1] = pos
        end
    end

    player.clear_cursor()

    return serialize({
        built = #placed,
        failed = #errors,
        positions = placed,
        errors = #errors > 0 and errors or nil,
    })
end