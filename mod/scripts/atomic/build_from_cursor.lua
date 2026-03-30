-- atomic/build_from_cursor.lua
-- Atom: Build entity from cursor at position.
-- Uses character.cursor_stack (works in headless mode!)
-- Args: {"x": 10, "y": 5, "direction": 2}
-- Returns: {"built": true} or {"error": "..."}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

local DIR_MAP = {
    [0] = defines.direction.north,
    [1] = defines.direction.east,
    [2] = defines.direction.south,
    [3] = defines.direction.west,
}

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local cursor = e.cursor_stack
    if not cursor or not cursor.valid_for_read then
        return serialize({error = "cursor is empty"})
    end

    local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
    local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')
    local dir = args_str:match('"direction"%s*:%s*([%d]+)')

    if not x or not y then
        return serialize({error = "missing x or y"})
    end

    local position = {x = tonumber(x), y = tonumber(y)}
    local direction = DIR_MAP[tonumber(dir) or 0] or defines.direction.north

    local item_name = cursor.name

    -- Check can place
    local surface = e.surface
    local force = e.force
    
    local can_place = surface.can_place_entity{
        name = item_name,
        position = position,
        direction = direction,
        force = force,
    }

    if not can_place then
        return serialize({
            error = "cannot place",
            reason = "collision or invalid location",
        })
    end

    -- Create entity
    local entity = surface.create_entity{
        name = item_name,
        position = position,
        direction = direction,
        force = force,
    }

    if entity and entity.valid then
        -- Consume from cursor
        cursor.count = cursor.count - 1
        if cursor.count == 0 then
            cursor.clear()
        end
        
        return serialize({
            built = true,
            item = item_name,
            position = position,
            cursor_remaining = cursor.valid_for_read and cursor.count or 0,
        })
    else
        return serialize({
            error = "entity creation failed",
        })
    end
end