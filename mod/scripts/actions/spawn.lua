-- actions/spawn.lua
-- Action: Spawn the agent character with optional items.
-- Flow: Create character → Insert starting items
-- Args: {"items": {"iron-chest": 10, "electric-mining-drill": 5}}
-- Returns: {"spawned": true, ...} or {"already_exists": true, ...}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    -- Check if already spawned
    local existing = agent.get()
    if existing then
        local pos = existing.position
        local inv = agent.get_inventory()
        local items = {}
        if inv then
            for _, item in pairs(inv.get_contents()) do
                items[#items + 1] = {name = item.name, count = item.count}
            end
        end
        return serialize({
            spawned = false,
            already_exists = true,
            position = {x = pos.x, y = pos.y},
            items = items,
        })
    end

    -- Create character
    local surface = game.surfaces["nauvis"]
    local force = game.forces["player"]
    local spawn_pos = {x = 0, y = 0}

    -- Use player position if available
    local player = game.players[1]
    if player and player.valid then
        spawn_pos = {x = player.position.x, y = player.position.y}
    end

    local character = surface.create_entity{
        name = "character",
        position = spawn_pos,
        force = force,
    }

    if not character or not character.valid then
        return serialize({error = "failed to create character"})
    end

    -- Store in global
    storage.agent = {
        entity = character,
        spawned_tick = game.tick,
    }

    -- Parse and insert items
    local items_inserted = {}
    if args_str and args_str ~= "" then
        local items_section = args_str:match('"items"%s*:%s*%{([^%}]*)%}')
        if items_section then
            for name, count in items_section:gmatch('"([^"]+)"%s*:%s*([%d]+)') do
                local inserted = character.insert{name = name, count = tonumber(count)}
                if inserted > 0 then
                    items_inserted[name] = inserted
                end
            end
        end
    end

    return serialize({
        spawned = true,
        position = {x = character.position.x, y = character.position.y},
        items = items_inserted,
    })
end