-- examples/setup_mining.lua
-- Example: Set up a basic mining station (drill + chest).
-- Demonstrates: finding resources, placing multiple related entities.
--
-- Usage: /agent examples.setup_mining {"ore_x": 10, "ore_y": 5}
--   Looks for iron-ore at position, places drill + chest

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

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
    local ore_x = args_str:match('"ore_x"%s*:%s*([%-%.%d]+)') or "0"
    local ore_y = args_str:match('"ore_y"%s*:%s*([%-%.%d]+)') or "0"

    local ore_pos = {x = tonumber(ore_x), y = tonumber(ore_y)}

    -- Find ore at position
    local surface = e.surface
    local ores = surface.find_entities_filtered{
        position = ore_pos,
        radius = 3,
        type = "resource",
    }

    if #ores == 0 then
        return serialize({error = "no ore found at position"})
    end

    local ore = ores[1]
    local ore_name = ore.name

    -- Check inventory for drill and chest
    local inv = agent.get_inventory()
    local have_drill = inv.get_item_count("electric-mining-drill")
    local have_chest = inv.get_item_count("iron-chest")

    if have_drill == 0 then
        return serialize({error = "no electric-mining-drill in inventory"})
    end
    if have_chest == 0 then
        return serialize({error = "no iron-chest in inventory"})
    end

    -- Calculate positions
    local drill_pos = {x = ore.position.x, y = ore.position.y}
    local chest_pos = {x = ore.position.x + 2, y = ore.position.y}

    -- Helper function to place entity
    local function place_entity(name, pos)
        -- Move if needed
        if agent.distance(e.position, pos) > REACH_DISTANCE then
            e.teleport({x = pos.x - 2, y = pos.y - 2})
        end

        -- Remove from inventory
        inv.remove{name = name, count = 1}

        -- Set cursor and build
        local cursor = player.cursor_stack
        cursor.set_stack{name = name, count = 1}

        local success = pcall(function()
            player.build_from_cursor{
                position = pos,
                direction = defines.direction.east,
                build_mode = defines.build_mode.real,
            }
        end)

        -- Check result
        if not cursor.is_empty() then
            inv.insert{name = name, count = cursor.count}
            cursor.clear()
            return false
        end

        return true
    end

    -- Place drill
    local drill_placed = place_entity("electric-mining-drill", drill_pos)
    if not drill_placed then
        return serialize({error = "failed to place drill"})
    end

    -- Place chest
    local chest_placed = place_entity("iron-chest", chest_pos)
    if not chest_placed then
        -- Rollback: mine the drill
        local drill = surface.find_entities_filtered{
            position = drill_pos,
            radius = 1,
            name = "electric-mining-drill",
        }
        if #drill > 0 then
            e.mine_entity(drill[1], true)
        end
        return serialize({error = "failed to place chest, drill recovered"})
    end

    return serialize({
        success = true,
        ore_type = ore_name,
        drill_position = drill_pos,
        chest_position = chest_pos,
    })
end