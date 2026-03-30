-- actions/place.lua
-- Action: Place an entity at position.
-- Flow (headless-compatible):
--   1. Check inventory has item
--   2. Move to position if needed
--   3. Check can_place_entity
--   4. Remove item from inventory
--   5. Create entity on surface
-- Args: {"name": "iron-chest", "x": 10, "y": 5, "direction": 2}
-- Returns: {"placed": true, ...} or {"error": "..."}
--
-- Note: In headless mode, we use surface.create_entity + inventory.remove
-- instead of player.build_from_cursor (which requires a connected player).
-- The constraints (inventory, reach, collision) are still enforced.

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

local DIR_MAP = {
    [0] = defines.direction.north,
    [1] = defines.direction.east,
    [2] = defines.direction.south,
    [3] = defines.direction.west,
}

local REACH_DISTANCE = 10

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local surface = e.surface
    local force = e.force

    -- Parse args
    local name = args_str:match('"name"%s*:%s*"([^"]+)"')
    local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
    local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')
    local dir = args_str:match('"direction"%s*:%s*([%d]+)')

    if not name or not x or not y then
        return serialize({error = "missing name, x, or y"})
    end

    local position = {x = tonumber(x), y = tonumber(y)}
    local direction = DIR_MAP[tonumber(dir) or 0] or defines.direction.north

    -- Step 1: Check inventory
    local inv = agent.get_inventory()
    local have = inv.get_item_count(name)
    if have == 0 then
        return serialize({
            error = "no item in inventory",
            item = name,
        })
    end

    -- Step 2: Check reach, auto-move if needed
    local dist = agent.distance(e.position, position)
    if dist > REACH_DISTANCE then
        local move_pos = {x = position.x - 2, y = position.y - 2}
        e.teleport(move_pos)
    end

    -- Step 3: Check can place (collision)
    local can_place = surface.can_place_entity{
        name = name,
        position = position,
        direction = direction,
        force = force,
    }

    if not can_place then
        return serialize({
            error = "cannot place",
            reason = "collision or invalid location",
            position = position,
        })
    end

    -- Step 4: Remove from inventory
    inv.remove{name = name, count = 1}

    -- Step 5: Create entity
    local entity = surface.create_entity{
        name = name,
        position = position,
        direction = direction,
        force = force,
    }

    if not entity or not entity.valid then
        -- Rollback: return item
        inv.insert{name = name, count = 1}
        return serialize({error = "entity creation failed"})
    end

    return serialize({
        placed = true,
        entity = {
            name = entity.name,
            type = entity.type,
            position = {x = entity.position.x, y = entity.position.y},
        },
    })
end