-- lib/agent.lua
-- Agent character management: singleton pattern for factorio-agent.
-- Unlike ai-companion's multi-companion system, we have exactly one agent.

local M = {}

-- Constants
M.REACH_DISTANCE = 10  -- Build distance
M.DEFAULT_SPAWN_POSITION = {x = 0, y = 0}

--- Get the agent character entity.
-- Returns nil if not spawned yet.
function M.get()
    if not storage.agent then return nil end
    local e = storage.agent.entity
    if e and e.valid then return e end
    -- Entity was destroyed, clear storage
    storage.agent = nil
    return nil
end

--- Get agent storage data (includes inventory snapshot, position history, etc).
function M.get_data()
    return storage.agent
end

--- Check if agent exists and is valid.
function M.exists()
    return M.get() ~= nil
end

--- Get agent's current position.
function M.get_position()
    local e = M.get()
    if not e then return nil end
    return {x = e.position.x, y = e.position.y}
end

--- Get agent's main inventory.
function M.get_inventory()
    local e = M.get()
    if not e then return nil end
    return e.get_inventory(defines.inventory.character_main)
end

--- Calculate distance between two positions.
function M.distance(a, b)
    local dx = a.x - b.x
    local dy = a.y - b.y
    return math.sqrt(dx * dx + dy * dy)
end

--- Check if position is within reach distance from agent.
function M.can_reach(target_pos)
    local agent_pos = M.get_position()
    if not agent_pos then return false, "no agent" end
    local dist = M.distance(agent_pos, target_pos)
    if dist > M.REACH_DISTANCE then
        return false, "too far (dist=" .. math.floor(dist) .. ", max=" .. M.REACH_DISTANCE .. ")"
    end
    return true, nil
end

--- Check if agent has enough items in inventory.
function M.has_item(name, count)
    local inv = M.get_inventory()
    if not inv then return false, "no inventory" end
    local have = inv.get_item_count(name)
    if have < count then
        return false, "not enough " .. name .. " (have=" .. have .. ", need=" .. count .. ")"
    end
    return true, nil
end

--- Insert items into agent's inventory.
function M.insert_item(name, count)
    local e = M.get()
    if not e then return 0 end
    return e.insert{name = name, count = count}
end

--- Remove items from agent's inventory.
function M.remove_item(name, count)
    local inv = M.get_inventory()
    if not inv then return 0 end
    return inv.remove{name = name, count = count}
end

--- Get agent's surface.
function M.get_surface()
    local e = M.get()
    if not e then return nil end
    return e.surface
end

--- Get agent's force.
function M.get_force()
    local e = M.get()
    if not e then return nil end
    return e.force
end

return M