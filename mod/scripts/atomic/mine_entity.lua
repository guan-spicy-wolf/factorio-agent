-- atomic/mine_entity.lua
-- Atom: Mine an entity at position.
-- Returns item to inventory automatically (like real mining).
-- Args: {"x": 10, "y": 5, "name": "iron-chest"}  (name optional)
-- Returns: {"mined": true, "entity": "..."} or {"error": "..."}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    local surface = e.surface
    local force = e.force

    local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
    local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')
    local name = args_str:match('"name"%s*:%s*"([^"]+)"')

    if not x or not y then
        return serialize({error = "missing x or y"})
    end

    local position = {x = tonumber(x), y = tonumber(y)}

    -- Find entity at position
    local filter = {
        position = position,
        radius = 1,
        force = force,
    }
    if name then
        filter.name = name
    end

    local entities = surface.find_entities_filtered(filter)

    -- Find closest destroyable entity
    local target = nil
    local min_dist = math.huge
    for _, ent in ipairs(entities) do
        if ent.valid and ent.type ~= "character" and ent.can_be_destroyed() then
            local dist = agent.distance(ent.position, position)
            if dist < min_dist then
                min_dist = dist
                target = ent
            end
        end
    end

    if not target then
        return serialize({error = "no entity to mine at position"})
    end

    local target_name = target.name
    local target_position = {x = target.position.x, y = target.position.y}

    -- Use character's mine_entity (real mining, returns items to inventory)
    local mined = e.mine_entity(target, true)  -- force = true

    if mined then
        return serialize({
            mined = true,
            entity = target_name,
            position = target_position,
        })
    else
        return serialize({
            error = "mining failed",
            reason = "out of range or unmineable",
        })
    end
end
