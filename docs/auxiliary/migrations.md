# Factorio Modding: Migrations

**Version:** 2.0.76  
**Category:** Auxiliary Docs

## Overview

Migrations are a mechanism to fix up save files created with older versions of the game or mods. They are essential when changing prototype types, correcting research/recipe states, or renaming entities after updates.

## Mod Structure & File Organization

Migration files must be placed in the mod's **`migrations`** folder. Supported file formats depend on the purpose:

*   **`.json`**: For changing one prototype into another (e.g., renaming).
*   **`.lua`**: For altering the loaded game state.

## Execution Lifecycle

### Execution Order
1.  **Mod Order:** Migrations are sorted by mod load order first.
2.  **File Name:** Within a mod, files are sorted by name using **lexicographical comparison**.
3.  **Type Priority:** All **JSON migrations** are applied before any **Lua migrations**.

### Tracking & Idempotency
*   **Persistence:** Each save file remembers which migrations (by name) from which mods have been applied.
*   **Single Execution:** The same migration will not be applied twice to a save file.
*   **New Mods:** When adding a mod to an existing save, all migration scripts for that mod will be run.
*   **Timing:** JSON migrations are applied as the map is being loaded.

## JSON Migrations

JSON migrations allow changing one prototype into another, typically used for renaming prototypes.

### Capabilities
*   **ID Types:** All `IDTypes` are available for migration.
*   **Ghost Entities:** Ghost entities are not always able to be migrated. If migration fails (e.g., due to type change or entity becoming unbuildable), the ghost is removed instead.

### Reference Validity & Behavior
*   **Entity Name Change:**
    *   The entity retains its previous `unit_number`.
    *   References to the entity saved in `global.storage` stay **valid**.
*   **Entity Type Change:**
    *   Results in a new `unit_number`.
    *   Entity references in `global.storage` become **invalid**.
*   **Prototype References:**
    *   References to the `prototype` saved in `global.storage` stay **valid** when changing type or name via migration.
    *   References become **invalid** if the prototype is removed.

### JSON Example
Renaming the `wall` entity and item to `stone-wall`:

```json
{
  "entity": [
    ["wall", "stone-wall"]
  ],
  "item": [
    ["wall", "stone-wall"]
  ]
}
```

## Lua Migrations

Lua migrations allow altering the loaded game state before it starts running.

### Capabilities
*   **Global Access:** The global `game` object is available, allowing modification of the game state.
*   **Timing:** Executed after JSON migrations, before the game resumes running.

### Notes on Recipes & Technologies
The game automatically resets `recipes` and `technologies` any time mods, prototypes, or startup settings change. Migration scripts **do not** need to handle resetting these manually anymore.

## Technical Summary

| Feature | JSON Migration | Lua Migration |
| :--- | :--- | :--- |
| **File Extension** | `.json` | `.lua` |
| **Primary Use** | Prototype renaming/type changing | Game state modification |
| **Execution Phase** | Map Load (Early) | Map Load (Late) |
| **Priority** | High (Runs before Lua) | Low (Runs after JSON) |
| **Game Object** | No | Yes (`game` available) |
| **Storage Validity** | Depends on operation (see above) | Managed by script logic |