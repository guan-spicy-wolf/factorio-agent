# Factorio Modding Documentation: Storage System

**Version:** 2.0.76  
**Category:** Auxiliary Docs / Runtime

## 1. Overview

During the lifetime of a mod, it is frequently necessary to save mutable data. While mods can store data in standard Lua variables, the game **will not persist** these variables through the save/load cycle.

To address this, Factorio provides the `storage` table.

*   **Persistence:** It is a Lua global variable that is serialized and persisted between saving and loading of a map.
*   **Scope:** Each mod has access to its own instance of this table. There is no need to worry about namespacing between mods.
*   **References:** Circular references within the table are handled properly.

## 2. Data Lifecycle & Restrictions

Accessing the `storage` table is subject to strict lifecycle restrictions to prevent desynchronization.

| Lifecycle Stage | `storage` State | Access Rules |
| :--- | :--- | :--- |
| **`control.lua` Load** | Not Restored | The table is not yet restored during the initial `control.lua` loading step. |
| **Before `on_load`** | Overwritten | The table is overwritten just before the `on_load` event fires. |
| **During `on_load`** | Restored (Read-Only) | **Writing to `storage` is disallowed.** Doing so will throw an error to prevent desyncs. |
| **Runtime** | Persistent | Safe to read and write during normal event handling (e.g., `on_tick`, `on_built_entity`). |

> **Warning:** Attempting to write to `storage` during the `on_load` event will result in an error.

## 3. Supported Data Types

Only specific data structures can be stored in `storage`. Unsupported types will cause errors during the save process.

### 3.1 Allowed Types
*   **Basic Data:** `nil`, strings, numbers, booleans.
*   **Tables:** Standard Lua tables.
*   **Factorio Objects:** References to Factorio's `LuaObject`s.

### 3.2 Forbidden Types
*   **Functions:** Functions are not allowed in `storage` and will throw an error when saving.

## 4. Metatable Handling

Tables with metatables have specific serialization rules:

1.  **Metatable Persistence:** The metatable itself is **not** saved.
2.  **Registered Metatables:**
    *   Metatables registered with `LuaBootstrap::register_metatable` are recorded by name.
    *   Upon loading, they are automatically relinked to the registered table.
3.  **Unregistered Metatables:**
    *   Any other metatables will be removed during serialization.
    *   Tables with unregistered metatables become **plain tables** when saved and loaded.

## 5. Version History

*   **Factorio Version < 2.0:** The `storage` table was previously named `global`.
*   **Factorio Version ≥ 2.0:** Renamed to `storage`.