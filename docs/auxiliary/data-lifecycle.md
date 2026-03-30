# Factorio Modding: Data Lifecycle & Structure

Factorio mods execute code at two distinct points: **Game Startup** (loading prototypes) and **Save Startup/Runtime** (actively playing a map). Each stage has specific rules, constraints, and file requirements.

## 1. Mod Loading Order & Dependencies

The game determines the execution order of active mods based on the following hierarchy:

1.  **Dependency Chain Depth:** Mods with shorter dependency chains are loaded first.
2.  **Natural Sort Order:** For mods with identical chain depths, internal mod names are sorted naturally.

This order dictates:
*   The load sequence at game startup.
*   The order in which events are sent during runtime.

> **Note:** **Instrument Mode** may be enabled for a single mod to inject code for development tools, impacting the data lifecycle for that specific mod.

---

## 2. Game Startup (Data Stage)

When Factorio launches, it loads all prototypes and assets. No actual game instance exists during this phase, and the standard runtime Lua API (e.g., `game`) is **not** available.

### 2.1. Execution Flow
For each stage (Settings and Prototype), a single shared Lua state is created. Each mod runs three specific files in sequence:

1.  `stage.lua`
2.  `stage-updates.lua`
3.  `stage-final-fixes.lua`

This consecutive rounding allows mods to influence other mods' prototypes without hard dependencies. Changes are tracked by the game. After the stage completes, the shared Lua state is discarded.

### 2.2. Settings Stage
**Files:** `settings.lua`, `settings-updates.lua`, `settings-final-fixes.lua`

*   **Purpose:** Define mod setting prototypes.
*   **Global Variables:**
    *   `data`: Table collecting prototypes (expects specific formats).
    *   `mods`: Mapping of mod name to version for all enabled mods.
*   **Outcome:** Settings prototypes are constructed. Player settings are read from `mod-settings.dat`.

### 2.3. Prototype Stage
**Files:** `data.lua`, `data-updates.lua`, `data-final-fixes.lua`

*   **Purpose:** Define all non-setting prototypes (entities, items, recipes, etc.).
*   **Global Variables:**
    *   `data`: Table for prototype definitions.
    *   `mods`: Map of enabled mods.
    *   `settings`: Populated with startup mod settings loaded from the Settings Stage. **Note:** Settings prototypes can no longer be modified.
*   **Outcome:** All prototypes are constructed. The game proceeds to the main menu.

---

## 3. Save Startup (Control Stage)

The control stage begins when a player starts a new game or loads a save. Prototypes cannot be modified, and the global `data` variable is inaccessible.

### 3.1. Initialization Flow
Upon loading a save, every mod goes through a specific sequence. The path depends on whether the mod is **New** to the save or **Existing**.

#### Step 1: `control.lua`
*   **When:** Every time a save is created or loaded.
*   **Action:** Loaded and executed in the mod's own persistent Lua state.
*   **Usage:** Register events (`script`), remote interfaces (`remote`), custom commands (`commands`).
*   **Restrictions:**
    *   `game` and `rendering` objects are **not** available.
    *   `storage` table exists but is **not** restored from save yet. Do not initialize `storage` here (use `on_init`).
*   **Benefit:** Changes to `control.lua` take effect without restarting the game (reload save).

#### Step 2 & 3: Init vs. Load & Migrations
The flow forks based on whether the mod is new to the save.

| Condition | Action Sequence |
| :--- | :--- |
| **Mod is New**<br>(New save OR added to existing game) | 1. Run `on_init()` handler.<br>2. Run applicable **Migrations**. |
| **Mod is Existing**<br>(Already present in save) | 1. Run applicable **Migrations**.<br>2. Run `on_load()` handler. |

*   **Migrations:**
    *   Runs JSON migrations (prototype renaming) then Lua migrations (state adjustment).
    *   Saves remember applied migration filenames to prevent re-running.
    *   Runs in mod's Lua state (temporary state created if no `control.lua`).
*   **`on_init()`**:
    *   Full access to `game` object and `storage`.
    *   Set up initial values for the mod's lifetime.
*   **`on_load()`**:
    *   **Critical:** Must be deterministic to avoid desyncs in multiplayer/replays.
    *   `game` object is **not** available.
    *   `storage` is **read-only** (writing causes errors).
    *   **Legitimate Uses:**
        1.  Re-setup metatables (not registered via `LuaBootstrap::register_metatable`).
        2.  Re-setup conditional event handlers.
        3.  Create local references to data in `storage`.

#### Step 4: `on_configuration_changed()`
*   **When:** Runs for **all** active mods if the save's configuration changed.
*   **Triggers:** Game version change, mod version change, mod added/removed, startup setting change, prototype change, or migration applied.
*   **Access:** Full access to `game` object and `storage` (read/write).
*   **Purpose:** Adjust internal data structures to match new configuration.

### 3.2. Multiplayer Join Logic
When a client downloads a save to join a running multiplayer session:
*   **Migrations:** Already applied on the server.
*   **Configuration Changed:** Already handled on the server.
*   **Execution:** Effectively only **Step 1 (`control.lua`)** and **Step 4 (`on_load()`)** run.
*   **Requirement:** Ensure event registrations and Lua state are identical to existing players to prevent desyncs.

---

## 4. API Access & Global Variables Summary

| Variable / Object | Settings Stage | Prototype Stage | Control Stage (`control.lua`) | `on_init` / `on_config` | `on_load` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `data` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `mods` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `settings` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `game` | ❌ | ❌ | ❌ | ✅ | ❌ |
| `storage` | ❌ | ❌ | ⚠️ (Not restored) | ✅ (Read/Write) | ⚠️ (Read Only) |
| `script` | ❌ | ❌ | ✅ | ✅ | ✅ |
| `rendering` | ❌ | ❌ | ❌ | ✅ | ✅ |

---

## 5. Recommended Mod Structure

Based on the lifecycle, a standard mod directory should look like:

```text
mod_name/
├── info.json              # Mod metadata & dependencies
├── settings.lua           # Define mod settings
├── settings-updates.lua   # Modify settings
├── settings-final-fixes.lua # Finalize settings
├── data.lua               # Define prototypes
├── data-updates.lua       # Modify prototypes
├── data-final-fixes.lua   # Finalize prototypes
├── control.lua            # Runtime logic & event registration
├── migrations/            # Migration scripts
│   ├── [version].json
│   └── [version].lua
└── locales/               # Localization files
```

## 6. Technical Notes

*   **Prototype History:** The game records a history of which mod changed which prototype during the data stage.
*   **Storage Persistence:** The `storage` table is persisted in the save file. It is restored just before `on_load`.
*   **Desync Prevention:** In `on_load`, do not modify game state or write to `storage`. Use `on_init`, `on_configuration_changed`, or event handlers for modifications.
*   **Scenario Scripts:** Treated like mods during the `on_init` step.