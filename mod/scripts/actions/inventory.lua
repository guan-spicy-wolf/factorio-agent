-- actions/inventory.lua
-- Action: Query or modify inventory (convenience wrapper).
-- Args: {} for query, {"check": "item-name"} for check, {"add": {...}} for add
-- Returns: varies by action

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local inv = agent.get_inventory()
    if not inv then
        return serialize({error = "no inventory"})
    end

    -- Determine action
    if not args_str or args_str == "" or args_str:match('^%s*%}%s*$') or not args_str:match('%S') then
        -- Query all items
        local items = {}
        for _, item in pairs(inv.get_contents()) do
            items[#items + 1] = {name = item.name, count = item.count}
        end
        table.sort(items, function(a, b) return a.count > b.count end)
        return serialize({
            items = items,
            free_slots = inv.count_empty_stacks(),
        })

    elseif args_str:match('"check"') then
        -- Check specific item
        local name = args_str:match('"check"%s*:%s*"([^"]+)"')
        if not name then
            return serialize({error = "missing item name"})
        end
        local count = inv.get_item_count(name)
        return serialize({
            item = name,
            count = count,
        })

    elseif args_str:match('"add"') then
        -- Add items (debug)
        local e = agent.get()
        local add_section = args_str:match('"add"%s*:%s*%{([^%}]*)%}')
        if add_section then
            local inserted = {}
            for name, count in add_section:gmatch('"([^"]+)"%s*:%s*([%d]+)') do
                local ins = e.insert{name = name, count = tonumber(count)}
                if ins > 0 then inserted[name] = ins end
            end
            return serialize({added = inserted})
        end
    end

    return serialize({error = "unknown action"})
end