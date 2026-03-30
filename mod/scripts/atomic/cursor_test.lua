-- atomic/cursor_test.lua
-- Test: Check if character has cursor_stack capability.
-- Args: (none)
-- Returns: {"has_cursor": true/false, ...}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({error = "agent not spawned"})
    end

    -- Check if cursor_stack exists
    local cs = e.cursor_stack
    if not cs then
        return serialize({
            has_cursor = false,
            reason = "cursor_stack is nil (no player attached)"
        })
    end

    -- Check if it's valid and has items
    local is_empty = not cs.valid_for_read or cs.count == 0
    
    return serialize({
        has_cursor = true,
        valid_for_read = cs.valid_for_read,
        is_empty = is_empty,
        item = not is_empty and cs.name or nil,
        count = not is_empty and cs.count or nil,
    })
end