-- actions/inspect.lua
-- Action: Inspect entities and resources in an area.
-- Args: {"x": 0, "y": 0, "radius": 10} or just number for radius around agent
-- Returns: {"entities": [...], "resources": [...], "agent_position": {...}}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

local MAX_RESULTS = 50

return function(args_str)
    local surface = game.surfaces["nauvis"]
    local agent_pos = agent.get_position()

    -- Default center on agent
    local center_x, center_y = 0, 0
    if agent_pos then
        center_x, center_y = agent_pos.x, agent_pos.y
    end

    local radius = 10

    -- Parse args
    local num = tonumber(args_str)
    if num then
        radius = num
    elseif args_str and args_str ~= "" then
        local x = args_str:match('"x"%s*:%s*([%-%.%d]+)')
        local y = args_str:match('"y"%s*:%s*([%-%.%d]+)')
        local r = args_str:match('"radius"%s*:%s*([%-%.%d]+)')
        if x then center_x = tonumber(x) end
        if y then center_y = tonumber(y) end
        if r then radius = tonumber(r) end
    end

    local area = {
        {center_x - radius, center_y - radius},
        {center_x + radius, center_y + radius},
    }

    -- Find entities
    local entities_raw = surface.find_entities_filtered{area = area}
    local entities = {}

    for i, ent in ipairs(entities_raw) do
        if i > MAX_RESULTS then break end
        if ent.valid and ent.type ~= "character" then
            entities[#entities + 1] = {
                name = ent.name,
                type = ent.type,
                position = {x = math.floor(ent.position.x * 10) / 10, y = math.floor(ent.position.y * 10) / 10},
            }
        end
    end

    -- Find resources
    local resources_raw = surface.find_entities_filtered{area = area, type = "resource"}
    local resources = {}

    for i, res in ipairs(resources_raw) do
        if i > 20 then break end
        if res.valid then
            resources[#resources + 1] = {
                name = res.name,
                position = {x = res.position.x, y = res.position.y},
                amount = res.amount,
            }
        end
    end

    local result = {
        entities = entities,
        entity_count = #entities,
        resources = resources,
        resource_count = #resources,
        center = {x = center_x, y = center_y},
        radius = radius,
    }

    if agent_pos then
        result.agent_position = {x = agent_pos.x, y = agent_pos.y}
    end

    return serialize(result)
end