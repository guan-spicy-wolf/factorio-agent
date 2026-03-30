# Instrument Mode

## Overview
Instrument Mode gives a mod the ability to inject extra code very early on in all Lua states. This is intended to be used to provide mod development tools and other instrumentation, in combination with the Lua `debug` library and the `LuaProfiler`.

## Activation
At most **one** mod may be enabled in Instrument Mode. Enable it using the following command line argument:

```bash
--instrument-mod modname
```

## Restrictions
*   **Multiplayer Disabled:** Multiplayer is disabled while an Instrument Mode mod is in use, as it is not desync-safe.

## Lifecycle & File Structure
The following additions to the usual **Data Lifecycle** apply when Instrument Mode is active:

### 1. Settings Stage
| File | Load Order | Description |
| :--- | :--- | :--- |
| `instrument-settings.lua` | **Before** all other mods | If present, loaded first. The settings stage then proceeds as normal. |

### 2. Data Stage
| File | Load Order | Description |
| :--- | :--- | :--- |
| `instrument-data.lua` | **Before** all other mods | If present, loaded before any other mod's data files. |
| `instrument-after-data.lua` | **After** all `data-final-fixes.lua` | If present, loaded after all other mods have completed their final data fixes. |

### 3. Control Stage
| File | Load Order | Description |
| :--- | :--- | :--- |
| `instrument-control.lua` | **Before** every mod's `control.lua` | Loaded in **every** mod's Lua state before their own control script. The control stage then proceeds as normal. |

## Error Handling
In all three instrument files (`instrument-settings.lua`, `instrument-data.lua`, `instrument-control.lua`), the additional global function `on_error` may be used to register an error handler for that Lua state.

### Function Signature
```lua
---@param error_object LocalisedString The thrown error object
---@return LocalisedString? Message to be added to the displayed error message
on_error(function(error_object)
    -- Custom error handling logic
    return {"instrument-mode-error-prefix"}
end)
```

## Related Documentation
*   Data Lifecycle
*   Mod Structure
*   Lua Debug Library
*   LuaProfiler