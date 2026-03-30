# Factorio Auxiliary Docs: Runtime JSON Format

**Version:** 2.0.76  
**API Version:** 6 (Introduced with Factorio 2.0.5)  
**Stage:** Runtime

## 1. Overview

The runtime API documentation is available in a machine-readable **JSON format**. This format allows for the creation of developer tools that provide code completion and related functionality.

> **Note on Data Lifecycle:** The `stage` field in this format will always be `"runtime"` (as opposed to `"prototype"`). For more detail on the distinction, refer to the **Data Lifecycle** documentation.

## 2. General Notes

The following rules apply to the JSON format structure:

*   **Null Handling:** If a member would be `null`, it is omitted from the JSON entirely.
*   **Sorting:** Every list is sorted alphabetically by `name`. To replicate the order seen on the website, sort by the `order` property of its members.
*   **Text Formatting:** Text (descriptions, examples, etc.) is formatted as **Markdown**, including links, inline code, and code blocks.

## 3. Link Format

All text fields can contain Markdown-type links. There are two categories:

### 3.1 External Links
Standard Markdown links starting with `https://`.
```markdown
[Factorio](https://factorio.com)
```

### 3.2 Internal Links
Custom shorthand format to refer to specific parts of the API documentation. They are not valid hyperlinks but use a specific syntax.

**Syntax:** `[Title](stage:MemberName::SubMember)`

1.  **Stage Prefix:** Indicates the namespace.
    *   `runtime:` (Classes, events, concepts, etc.)
    *   `prototype:` (Prototypes, types, etc.)
2.  **Member Name:** The API member being linked (e.g., `LuaGuiElement`, `RecipePrototype`).
    *   Can also link to auxiliary pages: `classes`, `events`, `concepts` (runtime) or `prototypes`, `types` (prototype).
3.  **Sub-Member (Optional):** Specifies a method, attribute, or property.
    *   Format: `::<name>`
    *   Example: `::results`

**Examples:**
```markdown
[LuaGuiElement](runtime:LuaGuiElement)
[results](prototype:RecipePrototype::results)
[concepts](runtime:concepts)
```

## 4. Top-Level JSON Structure

The root object contains context information and the API documentation organized by type.

| Field | Type | Description |
| :--- | :--- | :--- |
| `application` | `string` | Always `"factorio"`. |
| `application_version` | `string` | Game version (e.g., `"1.1.35"`). |
| `api_version` | `number` | Format version (incremented on changes). Current: `6`. |
| `stage` | `string` | Always `"runtime"`. |
| `classes` | `array[Class]` | List of classes (LuaObjects). |
| `events` | `array[Event]` | List of events. |
| `concepts` | `array[Concept]` | List of concepts used by the API. |
| `defines` | `array[Define]` | List of defines used by the game. |
| `global_objects` | `array[Parameter]` | Global variables serving as API entry points. |
| `global_functions` | `array[Method]` | Global functions providing specific functionality. |

## 5. Core Type Definitions

### 5.1 BasicMember
Several API members inherit from this base structure.

```json
{
  "name": "string",
  "order": "number",
  "description": "string",
  "lists": "array[string] (optional)",
  "examples": "array[string] (optional)",
  "images": "array[Image] (optional)"
}
```

### 5.2 Class
*Inherits from `BasicMember`*

```json
{
  "visibility": "array[string] (optional)", // e.g., ["space_age"]
  "parent": "string (optional)",            // Name of inherited class
  "abstract": "boolean",                    // True if never instantiated directly
  "methods": "array[Method]",
  "attributes": "array[Attribute]",
  "operators": "array[Operator]"            // call, index, or length
}
```

### 5.3 Event
*Inherits from `BasicMember`*

```json
{
  "data": "array[Parameter]",
  "filter": "string (optional)"             // Name of the filter concept
}
```

### 5.4 Concept
*Inherits from `BasicMember`*

```json
{
  "type": "Type"                            // Either a proper Type or "builtin"
}
```

### 5.5 Define
*Inherits from `BasicMember`*
Defines can be recursive (sub-defines).

```json
{
  "values": "array[DefineValue] (optional)",
  "subkeys": "array[Define] (optional)"
}
```

## 6. Common Structures

### 6.1 Type System
A type field can be a simple `string` (e.g., `"string"`, `"number"`) or a complex table.

**Complex Type Structure:**
```json
{
  "complex_type": "string", // Denotes the kind of complex type
  // Additional members depend on complex_type:
}
```

**Supported Complex Types:**

| Type | Structure |
| :--- | :--- |
| `type` | `{ "value": Type, "description": string }` |
| `union` | `{ "options": array[Type], "full_format": boolean }` |
| `array` | `{ "value": Type }` |
| `dictionary` / `LuaCustomTable` | `{ "key": Type, "value": Type }` |
| `table` | `{ "parameters": array[Parameter], "variant_parameter_groups": array[ParameterGroup], "variant_parameter_description": string }` |
| `tuple` | `{ "values": array[Type] }` |
| `function` | `{ "parameters": array[Type] }` |
| `literal` | `{ "value": union[string, number, boolean], "description": string }` |
| `LuaLazyLoadedValue` | `{ "value": Type }` |
| `LuaStruct` | `{ "attributes": array[Attribute] }` |

### 6.2 Parameter
```json
{
  "name": "string",
  "order": "number",
  "description": "string",
  "type": "Type",
  "optional": "boolean"
}
```

### 6.3 ParameterGroup
```json
{
  "name": "string",
  "order": "number",
  "description": "string",
  "parameters": "array[Parameter]"
}
```

### 6.4 Method
*Inherits from `BasicMember`*

```json
{
  "visibility": "array[string] (optional)",
  "raises": "array[EventRaised] (optional)",
  "subclasses": "array[string] (optional)",
  "parameters": "array[Parameter]",
  "variant_parameter_groups": "array[ParameterGroup] (optional)",
  "variant_parameter_description": "string (optional)",
  "variadic_parameter": "VariadicParameter (optional)",
  "format": "MethodFormat",
  "return_values": "array[Parameter]"
}
```

**MethodFormat:**
```json
{
  "takes_table": "boolean",
  "table_optional": "boolean (optional)"
}
```

**VariadicParameter:**
```json
{
  "type": "Type (optional)",
  "description": "string (optional)"
}
```

### 6.5 Attribute
*Inherits from `BasicMember`*

```json
{
  "visibility": "array[string] (optional)",
  "raises": "array[EventRaised] (optional)",
  "subclasses": "array[string] (optional)",
  "read_type": "Type (optional)",
  "write_type": "Type (optional)",
  "optional": "boolean"
}
```

### 6.6 EventRaised
```json
{
  "name": "string",
  "order": "number",
  "description": "string",
  "timeframe": "string", // "instantly", "current_tick", or "future_tick"
  "optional": "boolean"
}
```

### 6.7 DefineValue
```json
{
  "name": "string",
  "order": "number",
  "description": "string"
}
```

### 6.8 Image
```json
{
  "filename": "string",      // Located in /static/images/
  "caption": "string (optional)"
}
```

## 7. Basic Types

*   **string:** Identifier or Markdown-formatted text.
*   **number:** Integer or floating-point (JSON does not distinguish).
*   **boolean:** `true` or `false`.

## 8. Changelog

### Version 6 (Factorio 2.0.5)
*   Replaced `type`, `read`, and `write` fields on attributes with `read_type` and `write_type`.

### Version 5 (Factorio 1.1.108)
*   Added `filter` field to events.
*   Added `visibility` field to classes, methods, and attributes.
*   Changed `BasicMember` description fields: removed `notes`, added `lists` and `images`.
*   Changed `tuple` complex type to be an array of types in order.
*   Combined `variadic_type` and `variadic_description` into `variadic_parameter`.
*   Combined `takes_table` and `table_is_optional` into `format`.
*   Renamed `table_is_optional` to `table_optional` in `MethodFormat`.
*   Renamed `base_classes` to `parent` (single string) on classes.
*   Removed `builtin_types` top-level member; merged into `concepts` as type `"builtin"`.

### Version 4 (Factorio 1.1.89)
*   Changed internal references to include context (`runtime:` or `prototype:`).
*   Renamed special `struct` concept type to `LuaStruct`.

### Version 3 (Factorio 1.1.62)
*   Added `abstract` field to classes.
*   Added `optional` field to attributes.
*   Added `type`, `literal`, `tuple`, and `struct` types.
*   Added `global_functions` top-level member.
*   Renamed `variant` type to `union`.
*   Replaced `category` field on concepts with `type`.

### Version 2 (Factorio 1.1.54)
*   Added `raises` field to methods and attributes.
*   Replaced `return_type` and `return_description` with `return_values` array.
*   Removed `see_also` field from classes, events, concepts, methods, and attributes.

### Version 1 (Factorio 1.1.35)
*   First release.