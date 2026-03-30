-- actions/remove.lua
-- Action: Remove (mine) an entity at position (real player flow).
-- Flow:
--   1. Move to position if needed
--   2. Find entity
--   3. Mine entity (items returned to inventory automatically)
-- Args: {"x": 10, "y": 5, "name": "iron-chest"}  (name optional)
-- Returns: {"removed": true, "entity": "..."} or {"error": "..."}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

local REACH_DISTANCE = 10

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    -- Parse args
    local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
    local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')
    local name = args_str:match('"name"%s*:%s*"([^"]+)"')

    if not x or not y then
        return serialize({error = "missing x or y"})
    end

    local position = {x = tonumber(x), y = tonumber(y)}
    local surface = e.surface
    local force = e.force

    -- Step 1: Check reach, auto-move if needed
    local dist = agent.distance(e.position, position)
    if dist > REACH_DISTANCE then
        local move_pos = {x = position.x - 2, y = position.y - 2}
        e.teleport(move_pos)
    end

    -- Step 2: Find entity
    local filter = {position = position, radius = 1, force = force}
    if name then filter.name = name end

    local entities = surface.find_entities_filtered(filter)

    local target = nil
    local min_dist = math.huge
    for _, ent in ipairs(entities) do
        if ent.valid and ent.type ~= "character" and ent.can_be_destroyed() then
            local d = agent.distance(ent.position, position)
            if d < min_dist then
                min_dist = d
                target = ent
            end
        end
    end

    if not target then
        return serialize({error = "no entity at position"})
    end

    -- Step 3: Mine entity (real mining)
    -- mine_entity returns items to inventory automatically
    local mined = e.mine_entity(target, true)

    if mined then
        return serialize({
            removed = true,
            entity = target.name,
            position = {x = target.position.x, y = target.position.y},
        })
    else
        return serialize({
            error = "mining failed",
            reason = "out of range or unmineable",
        })
    end
end