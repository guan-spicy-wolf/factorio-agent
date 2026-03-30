-- serialize.lua
-- Converts Lua values to JSON strings.
-- Handles: nil, bool, number, string, table (array or object).
-- Does NOT handle LuaObjects directly — callers must extract plain data first.

local serialize  -- forward declaration for recursion

local function serialize_string(s)
    -- Escape special JSON characters
    s = s:gsub('\\', '\\\\')
    s = s:gsub('"', '\\"')
    s = s:gsub('\n', '\\n')
    s = s:gsub('\r', '\\r')
    s = s:gsub('\t', '\\t')
    return '"' .. s .. '"'
end

local function is_array(t)
    local n = #t
    if n == 0 then
        -- Check if it's truly empty or an object with string keys
        for _ in pairs(t) do
            return false
        end
        return true  -- empty table → empty array
    end
    for k in pairs(t) do
        if type(k) ~= "number" or k < 1 or k > n or k ~= math.floor(k) then
            return false
        end
    end
    return true
end

serialize = function(value)
    local t = type(value)

    if value == nil then
        return "null"
    elseif t == "boolean" then
        return value and "true" or "false"
    elseif t == "number" then
        if value ~= value then return "null" end  -- NaN
        if value == math.huge then return "1e308" end
        if value == -math.huge then return "-1e308" end
        -- Use integer format when possible
        if value == math.floor(value) and math.abs(value) < 2^53 then
            return string.format("%d", value)
        end
        return string.format("%.17g", value)
    elseif t == "string" then
        return serialize_string(value)
    elseif t == "table" then
        if is_array(value) then
            local parts = {}
            for i = 1, #value do
                parts[i] = serialize(value[i])
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, v in pairs(value) do
                parts[#parts + 1] = serialize_string(tostring(k)) .. ":" .. serialize(v)
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    else
        return '"<' .. t .. '>"'
    end
end

return serialize
