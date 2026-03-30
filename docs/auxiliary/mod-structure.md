# Factorio Mod Structure & Configuration

Mods must adhere to a specific file structure and configuration format to be loaded by the game. This document outlines the required directory layout, mandatory files, and configuration schemas.

## 1. Mod Packaging & Naming

Mods can be distributed as a folder or a ZIP archive.

| Format | Naming Pattern | Example | Notes |
| :--- | :--- | :--- | :--- |
| **Folder** | `{mod-name}_{version}` or `{mod-name}` | `better-armor_0.3.6` | Folder inside must match mod name defined in `info.json`. |
| **ZIP** | `{mod-name}_{version}.zip` | `better-armor_0.3.6.zip` | Internal folder name has no restrictions, but ZIP filename must match pattern. |

*   **Mod Name & Version:** Defined in `info.json`.
*   **Constraints:** Mod names must be alphanumeric, dashes, or underscores. Max 100 chars (Portal: 3-50 chars).

## 2. File Structure

The game automatically reads specific files and folders within the mod directory.

### 2.1 Core Files

| File | Purpose | Mandatory |
| :--- | :--- | :--- |
| `info.json` | Identifies mod, version, and properties. | **Yes** |
| `changelog.txt` | Version history for mod browser. | No (Strict formatting required) |
| `thumbnail.png` | Image for mod portal/browser (Ideally 144x144px). | No |
| `settings.lua` | Define mod configuration options. | No |
| `settings-updates.lua` | Update configuration options. | No |
| `settings-final-fixes.lua` | Finalize configuration options. | No |
| `data.lua` | Define prototypes. | No |
| `data-updates.lua` | Update prototypes (Load order specific). | No |
| `data-final-fixes.lua` | Finalize prototypes (Load order specific). | No |
| `control.lua` | Runtime scripting logic. | No |

### 2.2 Recognized Subfolders

| Folder | Purpose |
| :--- | :--- |
| `locale` | Contains subfolders per language code (e.g., `en`). Must contain `*.cfg` files for translations. |
| `scenarios` | Custom scenarios placed in subfolders. |
| `campaigns` | Custom campaigns placed in subfolders. |
| `tutorials` | Custom tutorial scenarios placed in subfolders. |
| `migrations` | Migration files to handle prototype/data structure changes between versions. |

### 2.3 Example Directory Tree

```text
better-armor_0.3.6.zip
└── aFolderName/
    ├── control.lua
    ├── data.lua
    ├── info.json
    └── thumbnail.png
```

## 3. Configuration: info.json

The `info.json` file identifies the mod. Parsing errors are logged in the game log file.

### 3.1 Minimal Example

```json
{
  "name": "better-armor",
  "version": "0.3.6",
  "title": "My better armor mod",
  "author": "A very great modder",
  "factorio_version": "2.0",
  "dependencies": ["? optional-mod"]
}
```

### 3.2 Field Schema

| Field | Type | Mandatory | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `name` | string | **Yes** | - | Internal mod name. Alphanumeric, dashes, underscores only. |
| `version` | string | **Yes** | - | Format: `"Major.Middle.Minor"` (e.g., `"0.6.4"`). Range: 0-65535 per number. |
| `title` | string | **Yes** | - | Human-readable display name. Max 100 chars (override via locale `mod-name`). |
| `author` | string | **Yes** | - | Author name. No restrictions. Portal displays uploader name instead. |
| `contact` | string | No | - | Contact info (e.g., email). |
| `homepage` | string | No | - | URL (website, forum, Discord). Displayed as clickable link. |
| `description` | string | No | - | Short description. Displayed in-game. Override via locale `mod-description`. |
| `factorio_version` | string | No | `"0.12"` | Supported game version `"Major.Minor"` (e.g., `"2.0"`). Includes all `.sub` versions. |
| `dependencies` | array | No | `["base"]` | List of dependency strings. Empty array `[]` means no dependencies. |
| `*_required` | boolean | No | `false` | Feature flags requiring Space Age expansion (see Section 4). |

## 4. Dependencies

Defines load order and compatibility. If a mod depends on another, the other loads first.

### 4.1 Syntax

Format: `"<prefix> internal-mod-name <equality-operator> <version>"`

*   **Prefixes:**
    *   (none): Hard requirement.
    *   `!`: Incompatibility (version ignored).
    *   `?`: Optional dependency.
    *   `(?)`: Hidden optional dependency.
    *   `~`: Dependency that does not affect load order.
*   **Operators:** `<`, `<=`, `=`, `>=`, `>`
*   **Version:** Optional. If used with optional dependency (`?`), mod is disabled if dependency exists but version mismatch occurs.

### 4.2 Examples

```json
"dependencies": [
  "mod-a",                 // Hard requirement
  "? mod-c > 0.4.3",       // Optional, requires version > 0.4.3
  "! mod-g",               // Incompatible with mod-g
  "~ mod-h"                // Depends on mod-h but load order unaffected
]
```

## 5. Expansion Feature Flags

Boolean flags in `info.json` to enable features requiring the **Space Age** expansion.

| Field | Feature | Enabled Prototypes/Properties |
| :--- | :--- | :--- |
| `quality_required` | Quality | `QualityPrototype::level > 0`, `QualityPrototype::name` != "normal" |
| `rail_bridges_required` | Elevated Rails | `RailRampPrototype`, `RailSupportPrototype`, `Elevated*RailPrototype` |
| `spoiling_required` | Spoiling | `ItemPrototype::spoil_result`, `spoil_ticks`, `spoil_to_trigger_result` |
| `freezing_required` | Freezing | `EntityPrototype::heating_energy`, `PlanetPrototype::entities_require_heating` |
| `segmented_units_required` | Segmented Units | `SegmentedUnitPrototype`, `SegmentPrototype` |
| `expansion_shaders_required` | Additional Shaders | `TileEffectDefinition::space`, `SurfaceRenderParameters::fog`, etc. |
| `space_travel_required` | Space Travel | All prototypes/properties marked as requiring Space Age not covered above. |

## 6. Data Lifecycle Overview

The load order of data files is critical for prototype definition and modification.

1.  `data.lua`
2.  `data-updates.lua`
3.  `data-final-fixes.lua`

*   **Settings Files:** `settings.lua`, `settings-updates.lua`, `settings-final-fixes.lua` handle configuration options.
*   **Runtime:** `control.lua` is used for runtime scripting.
*   **Migrations:** Files in the `migrations` folder handle prototype changes between mod versions.