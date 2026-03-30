# Factorio Prototype JSON Format Specification

**Version:** 2.0.76  
**API Version:** 6 (Introduced in Factorio 2.0.5)  
**Stage:** Prototype (Data Lifecycle Stage)

## 1. Overview

The Prototype API documentation is available in a machine-readable JSON format. This format allows for the creation of developer tools (e.g., code completion, static analysis) by describing the structure of Factorio's prototypes, types, and defines.

### 1.1 General Notes

*   **Null Handling:** If a member would be `null`, it is omitted from the JSON entirely.
*   **Empty Descriptions:** Descriptions are generally empty strings (`""`) instead of `null` if they exist but are empty.
*   **Sorting:**
    *   Every list is sorted alphabetically by `name`.
    *   To replicate the order seen on the website, sort by the `order` property of members.
*   **Text Formatting:** Text (descriptions, examples) is formatted as **Markdown** (links, inline code, code blocks).

### 1.2 Link Format

Text fields may contain Markdown-style links.

#### External Links
Standard Markdown hyperlinks starting with `https://`.
```markdown
[Factorio](https://factorio.com)
```

#### Internal Links
Custom shorthand format referring to specific parts of the API documentation.
**Format:** `[Title](stage:MemberName::SubMember)`

1.  **Stage Prefix:**
    *   `runtime:`: Refers to classes, events, etc. (e.g., `LuaGuiElement`, `on_player_created`).
    *   `prototype:`: Refers to prototypes and types (e.g., `RecipePrototype`, `EnergySource`).
2.  **Member Name:** The specific API member (e.g., `LuaGuiElement`, `Concepts`, `prototypes`).
3.  **Sub-Member (Optional):** Specifies a method, attribute, or property using `::`.
    *   Example: `[results](prototype:RecipePrototype::results)`

---

## 2. Top-Level JSON Structure

The root object contains context information and the API documentation organized by type.

| Field | Type | Description |
| :--- | :--- | :--- |
| `application` | `string` | Always `"factorio"`. |
| `application_version` | `string` | Game version (e.g., `"1.1.90"`). |
| `api_version` | `number` | Format version (incremented on format changes). Current: `6`. |
| `stage` | `string` | Documentation stage. Always `"prototype"` for this format. |
| `prototypes` | `array[Prototype]` | List of creatable prototypes. |
| `types` | `array[Type]` | List of types/concepts used by the format. |
| `defines` | `array[Define]` | List of game defines. |

---

## 3. Core Object Definitions

### 3.1 BasicMember (Inheritance Base)
Most API members inherit these basic fields.

*   `name` :: `string`: The name of the member.
*   `order` :: `number`: Display order in HTML.
*   `description` :: `string`: Text description (Markdown). Never `null`.
*   `lists` :: `array[string]` (Optional): Markdown lists (often in spoiler tags).
*   `examples` :: `array[string]` (Optional): Code-only examples.
*   `images` :: `array[Image]` (Optional): Illustrative images.

### 3.2 Prototype
Represents a creatable game prototype (e.g., `item`, `entity`). Inherits from `BasicMember`.

*   `visibility` :: `array[string]` (Optional): Required expansions (e.g., `"space_age"`).
*   `parent` :: `string` (Optional): Name of the parent prototype.
*   `abstract` :: `boolean`: If `true`, cannot be created directly.
*   `typename` :: `string` (Optional): Type name (e.g., `"boiler"`). `null` for abstract.
*   `instance_limit` :: `number` (Optional): Max instances creatable.
*   `deprecated` :: `boolean`: If `true`, should not be used.
*   `properties` :: `array[Property]`: List of properties.
*   `custom_properties` :: `CustomProperties` (Optional): User-addable properties (key/value types).

### 3.3 Type / Concept
Represents a data type or concept. Inherits from `BasicMember`.

*   `parent` :: `string` (Optional): Name of the parent type.
*   `abstract` :: `boolean`: If `true`, cannot be created directly.
*   `inline` :: `boolean`: If `true`, inlined inside another property's description.
*   `type` :: `Type`: The type of the type (either a proper `Type` object or `"builtin"`).
*   `properties` :: `array[Property]` (Optional): Properties if the type includes a struct. `null` otherwise.

### 3.4 Define
Represents game constants/defines. Can be recursive. Inherits from `BasicMember`.

*   `values` :: `array[DefineValue]` (Optional): Members of the define.
*   `subkeys` :: `array[Define]` (Optional): Sub-defines (recursive structure).

---

## 4. Common Structures

### 4.1 Property
Defines a specific property of a Prototype or Type. Inherits from `BasicMember`.

*   `visibility` :: `array[string]` (Optional): Required expansions.
*   `alt_name` :: `string` (Optional): Alternative name for reference.
*   `override` :: `boolean`: If `true`, overrides a parent property of the same name.
*   `type` :: `Type`: The data type of the property.
*   `optional` :: `boolean`: If `true`, can be omitted (falls back to default).
*   `default` :: `union[string, Literal]` (Optional): Default value (text description or literal).

### 4.2 Type Definition
A type field can be a simple `string` (builtin) or a complex table.

**Complex Type Structure:**
*   `complex_type` :: `string`: Kind of complex type.

**Specific Complex Types:**
*   **array**:
    *   `value` :: `Type`: Element type.
*   **dictionary**:
    *   `key` :: `Type`: Key type.
    *   `value` :: `Type`: Value type.
*   **tuple**:
    *   `values` :: `array[Type]`: Member types in order.
*   **union**:
    *   `options` :: `array[Type]`: Compatible types.
    *   `full_format` :: `boolean`: Whether options have descriptions.
*   **literal**:
    *   `value` :: `union[string, number, boolean]`: The literal value.
    *   `description` :: `string` (Optional): Description of the literal.
*   **type**:
    *   `value` :: `Type`: The actual type.
    *   `description` :: `string`: Description of the type.
*   **struct**:
    *   Special type. Properties are listed on the API member using this type.

### 4.3 Literal
Same format as a complex `Type` of kind `literal`.
```json
{"complex_type": "literal", "value": 2}
```

### 4.4 Image
*   `filename` :: `string`: File name (located in `/static/images/`).
*   `caption` :: `string` (Optional): Explanatory text.

### 4.5 Custom Properties
Specifies user-addable property constraints.
*   `description` :: `string`
*   `lists` :: `array[string]` (Optional)
*   `examples` :: `array[string]` (Optional)
*   `images` :: `array[Image]` (Optional)
*   `key_type` :: `Type`
*   `value_type` :: `Type`

### 4.6 DefineValue
*   `name` :: `string`
*   `order` :: `number`
*   `description` :: `string`

---

## 5. Basic Types

*   **string**: Identifier or Markdown-formatted text.
*   **number**: Integer or floating-point (JSON does not distinguish).
*   **boolean**: `true` or `false`.

---

## 6. Changelog

### Version 6 (Factorio 2.0.5)
*   Added `defines` as a top-level member.

### Version 5 (Factorio 1.1.108)
*   Added `visibility` field to prototypes and properties.

### Version 4 (Factorio 1.1.89)
*   First release.