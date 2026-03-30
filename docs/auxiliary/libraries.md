# Factorio Auxiliary Docs: Libraries and Functions

**Version:** 2.0.76  
**Base Lua Version:** 5.2.1 (Modified)

Factorio extends the standard Lua environment with custom libraries and functions to support modding. Crucially, several standard functions are modified to ensure **determinism** across different platforms and executions.

---

## 1. Provided Libraries

### `serpent`
Factorio provides the `serpent` library as a global variable for all mods. It is used for debugging and printing Lua tables.

*   **Usage:** `serpent.block()` allows easy printing of tables.
*   **Limitations:** Cannot pretty-print LuaObjects (e.g., `LuaEntity`).
*   **Determinism Modifications:**
    *   Comments are turned off by default to avoid returning table addresses.
    *   **New Options:**
        *   `refcomment` (`true`/`false`/`maxlevel`): Controls self-reference output.
        *   `tablecomment` (`true`/`false`/`maxlevel`): Controls table value output.

### `string`
The `string` library includes functions backported from **Lua 5.4.6** for handling binary structure format strings:

*   `string.pack`
*   `string.packsize`
*   `string.unpack`

---

## 2. New Global Functions

### `log(LocalisedString)`
Prints a `LocalisedString` to the Factorio **log file**. Essential for debugging the data stage.

**Example:**
```lua
-- Print all properties of the sulfur item prototype
log(serpent.block(data.raw["item"]["sulfur"]))
```

### `localised_print(LocalisedString)`
Prints a `LocalisedString` to **stdout** without polluting the Factorio log file.
*   **Use Case:** Communicating with external tools that launch Factorio as a child process.

### `table_size(table) → uint32`
Determines the size of tables with non-continuous keys. The standard `#` operator does not work correctly for such tables.

*   **Implementation:** C++ implementation (faster than Lua iteration).
*   **Logic Equivalent:**
    ```lua
    local function size(t)
        local count = 0
        for k, v in pairs(t) do
            count = count + 1
        end
        return count
    end
    ```
*   **Warning:** Does **not** work correctly for `LuaCustomTable`. Use `LuaCustomTable::length_operator` instead.

---

## 3. Modified Standard Functions

To ensure determinism and security, several standard Lua functions behave differently in Factorio.

### `pairs()` / `next()`
In standard Lua, iteration order is arbitrary. In Factorio, iteration order is **deterministic**.

*   **Ordering Rule:** Depends on insertion order (keys inserted first are iterated first).
*   **Numeric Keys:** The first **1024 numbered keys** are iterated from `1` to `1024`, regardless of insertion order.
*   **Impact:** For common uses, `pairs()` has no drawbacks compared to `ipairs()`.

### `require()`
Functionality changes due to modifications in the `package` module.

*   **Path Resolution:**
    *   Absolute paths start at the **mod root**.
    *   `..` is **disabled** (cannot load files outside the mod directory).
    *   Files must end with the `.lua` extension.
*   **Cross-Mod Loading:**
    1.  **Core Libs:** The `lualib` directory of the core mod is included in paths (e.g., `require("util")`).
    2.  **Other Mods:** Use the syntax `require("__mod-name__.file")`.
*   **Restrictions:** Cannot be used in:
    *   The console.
    *   Event listeners.
    *   During a `remote.call()`.

### `print()`
Outputs to **stdout**.
*   **Behavior:** Does not end up in the Factorio log file. Only visible when starting Factorio from the command line.
*   **Recommendation:** Use `log()` or `LuaGameScript::print` for debugging.

### `math.random()` & `math.randomseed()`
Reimplemented to ensure determinism in both data stage and runtime.

*   **Data Stage:** Seeded with a constant number.
*   **Runtime:** Uses the map's global random generator (seeded with the map seed). Shared between all mods and the core game.
*   **Isolation:** If independent randomness is required, use `LuaRandomGenerator`.
*   **Constraints:**
    *   Cannot be used outside of events or during loading.
    *   Non-integer arguments are floored instead of causing an error.
*   **`math.randomseed()`:** Has **no effect** in Factorio. Use `LuaRandomGenerator` for custom seeding.

### `load()`
*   **Binary Chunks:** Will not load binary chunks.
*   **Mode Argument:** Has no effect.

### `debug`
Access is restricted for security and determinism.

*   **Available by Default:**
    *   `debug.getinfo()`
    *   `debug.traceback()`
*   **Advanced Access:** Potentially unsafe functions can be re-enabled via a command line option.
*   **`debug.getinfo()` Extension:**
    *   Supports additional flag `p`.
    *   Fills `currentpc` with the index of the current instruction within the function at the given stack level (or `-1` for functions not on the call stack).

### Mathematical Functions
All trigonometric, hyperbolic, exponential, and logarithmic functions are replaced by custom implementations to ensure **determinism across platforms**. Behavior is equivalent to standard Lua for standard uses.

---

## 4. Inaccessible Modules

The following standard Lua modules are **not accessible** to ensure determinism and security:

*   `loadfile()`
*   `dofile()`
*   `coroutine`
*   `io`
*   `os`

*Note: Factorio provides its own versions of `package` and `debug`.*