# Factorio Modding Documentation: Noise Expressions

**Version:** 2.0.76  
**Category:** Auxiliary Docs / Map Generation

## 1. Overview

Noise expressions represent a series of mathematical expressions which are executed for every tile during map generation. Mods can define their own noise expressions and functions as prototypes and tell the game to use them for map generation.

**Major Entry Points:**
*   `AutoplaceSpecification::probability_expression`
*   `AutoplaceSpecification::richness_expression`
*   `NamedNoiseExpression`

See the `NoiseExpression` Type for syntax details.

---

## 2. Identifiers & Naming

Identifiers are used to name functions and variables which have their own (return) types.

### 2.1. Data Types
| Type | Description |
| :--- | :--- |
| `Number` | Usually a single-precision floating-point number. |
| `String` | Text identifier. |
| `MapPosition` | A position on the map. |
| `MapPositionList` | An array of `MapPosition` variables. |
| `Boolean` | Stored as a number; `true` for positive numbers, `false` for negative numbers and zero. |
| `NoiseLayerID` | Stored as a number or a string; string is converted to ID using CRC32. |

### 2.2. Name Resolution
The game engine does not restrict naming of noise functions and noise expressions, although some names may be unusable because of the parser. Function parameters are also parsed as identifiers. It is recommended to follow standard identifier formats.

**Proxy Access:**
If an expression cannot be parsed as an identifier (e.g., contains hyphens), it can be accessed via a proxy function `var()`.
*   **Invalid:** `my-noise-expression` (Parsed as subtraction: `my` - `noise` - `expression`)
*   **Valid:** `var('my-noise-expression')`

*Note: Function names and function parameters do not have alternative ways of accessing them.*

### 2.3. Name Collisions
The parser resolves name collisions in the following order:
1.  Try to find the most local noise expression/function, taking into account function parameters.
2.  Check `property_expression_names` defined in `MapGenSettings`.
3.  Check global prototype names (`named noise expressions` / `functions`).
4.  Check built-in constants, variables and functions.

---

## 3. Environment

### 3.1. Built-in Variables
Noise expressions of all tiles, entities and decoratives which will be generated on map can also be accessed as variables. By default, noise expressions from prototype autoplace specification are used, but they can be overwritten with `MapGenSettings::property_expression_names`.

| Identifier | Type | Description |
| :--- | :--- | :--- |
| `x` | Number | Current X position on the map. |
| `y` | Number | Current Y position on the map. |
| `decorative:<name>:probability` | Number | Probability expression for a decorative. |
| `decorative:<name>:richness` | Number | Richness expression for a decorative. |
| `entity:<name>:probability` | Number | Probability expression for an entity. |
| `entity:<name>:richness` | Number | Richness expression for an entity. |
| `tile:<name>:probability` | Number | Probability expression for a tile. |
| `tile:<name>:richness` | Number | Richness expression for a tile. |

### 3.2. Built-in Constants

#### Parser Constants
| Identifier | Type | Value |
| :--- | :--- | :--- |
| `true` | Number | 1 |
| `false` | Number | 0 |
| `e` | Number | Euler's number |
| `pi` | Number | ~3.14159 |
| `inf` | Number | Infinity |

#### MapGenSettings Constants
| Identifier | Type | Description |
| :--- | :--- | :--- |
| `map_seed` | Number | 32-bit unsigned integer. |
| `map_seed_small` | Number | 16 least significant bits from `map_seed`. |
| `map_seed_normalized` | Number | 0-1 normalized value of `map_seed`. |
| `map_width` | Number | Map width. |
| `map_height` | Number | Map height. |
| `starting_area_radius` | Number | Starting area radius. |
| `cliff_elevation_0` | Number | Cliff elevation base. |
| `cliff_elevation_interval` | Number | Cliff elevation interval. |
| `cliff_smoothing` | Number | Cliff smoothing value. |
| `cliff_richness` | Number | Cliff richness value. |
| `starting_positions` | MapPositionList | List of starting positions. |
| `starting_lake_positions` | MapPositionList | Calculated from starting positions and map seed. |
| `peaceful_mode` | Boolean | Peaceful mode setting. |
| `no_enemies_mode` | Boolean | No enemies mode setting. |

#### Autoplace Controls
In addition to hard-coded values, all **autoplace controls** are converted to frequency, size and richness constants. Because their name contains '-' character, they have to be accessed via `var()` function.

*   `control:moisture:frequency`
*   `control:moisture:bias`
*   `control:aux:frequency`
*   `control:aux:bias`
*   `control:temperature:frequency`
*   `control:temperature:bias`
*   `control:<autoplace-name>:frequency`
*   `control:<autoplace-name>:size`
*   `control:<autoplace-name>:richness`

---

## 4. Built-in Functions

### Mathematical Functions

#### `abs(value)`
Returns absolute value of the given argument.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `ceil(value)`
Rounds a number up to the nearest integer.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `floor(value)`
Rounds a number down to the nearest integer.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `clamp(value, min, max)`
The first argument is clamped between the second and third.
*   **Parameters:** `value` (Number), `min` (Number), `max` (Number)
*   **Returns:** Number

#### `ridge(value, min, max)`
Similar to clamp but the input value is folded back across the upper and lower limits until it lies between them.
*   **Parameters:** `value` (Number), `min` (Number), `max` (Number)
*   **Returns:** Number

#### `max(...)`
Returns the greatest of the given values. Accepts up to 255 positional arguments.
*   **Parameters:** 2 to 255 Numbers (Positional only)
*   **Returns:** Number

#### `min(...)`
Returns the smallest of the given values. Accepts up to 255 positional arguments.
*   **Parameters:** 2 to 255 Numbers (Positional only)
*   **Returns:** Number

#### `log2(value)`
Returns a binary logarithm of the given value.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `sqrt(value)`
Returns the square root of the given parameter.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `pow(value, exponent)`
Fast (inaccurate) exponentiation from `fastapprox` library. Same as `x ^ y` operator.
*   **Parameters:** `value` (Number), `exponent` (Number)
*   **Returns:** Number

#### `pow_precise(value, exponent)`
Precise (but very slow) exponentiation.
*   **Parameters:** `value` (Number), `exponent` (Number)
*   **Returns:** Number

### Trigonometric Functions

#### `cos(value)`
The cosine trigonometric function.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `sin(value)`
The sine trigonometric function.
*   **Parameters:** `value` (Number)
*   **Returns:** Number

#### `atan2(y, x)`
Returns the arc tangent of y/x using the signs of arguments to determine the correct quadrant.
*   **Parameters:** `y` (Number), `x` (Number)
*   **Returns:** Number

### Noise Functions

#### `basis_noise(x, y, seed0, seed1, ...)`
A Factorio single-octave noise implementation.
*   **Parameters:**
    *   `x`, `y` (Number)
    *   `seed0` (Number; constant; 32-bit unsigned)
    *   `seed1` (NoiseLayerID; constant)
    *   `input_scale` (Number; constant; default = 1)
    *   `output_scale` (Number; constant; default = 1)
    *   `offset_x`, `offset_y` (Number; constant; default = 0)
*   **Returns:** Number
*   **Example:**
    ```lua
    basis_noise{x = x, y = y, seed0 = map_seed, seed1 = 0, input_scale = 1/3, output_scale = 3}
    ```

#### `multioctave_noise(x, y, persistence, seed0, seed1, octaves, ...)`
A Factorio multi-octave noise implementation. Uses the same algorithm as `basis_noise` but runs multiple layers.
*   **Parameters:**
    *   `x`, `y` (Number)
    *   `persistence` (Number; constant)
    *   `seed0`, `seed1` (Constants)
    *   `octaves` (Number; constant 32-bit unsigned)
    *   `input_scale`, `output_scale` (Number; constant; default = 1)
    *   `offset_x`, `offset_y` (Number; constant; default = 0)
*   **Returns:** Number
*   **Example:**
    ```lua
    multioctave_noise{x = x, y = y, persistence = 0.75, seed0 = map_seed, seed1 = 0, octaves = 4, input_scale = 1/3, output_scale = 3}
    ```

#### `quick_multioctave_noise(...)`
An alternative Factorio multi-octave noise implementation. Prefer regular `multioctave_noise` if possible.
*   **Parameters:** Similar to `multioctave_noise` plus `octave_input_scale_multiplier`, `octave_output_scale_multiplier`, `octave_seed0_shift`.
*   **Returns:** Number

#### `variable_persistence_multioctave_noise(...)`
Same as `multioctave_noise` except that it supports variable persistence.
*   **Parameters:** `persistence` is not constant.
*   **Returns:** Number

#### `spot_noise(...)`
Generates random conical spots. The map is divided into square regions.
*   **Parameters:**
    *   `x`, `y` (Number)
    *   `density_expression`, `spot_quantity_expression`, `spot_radius_expression`, `spot_favorability_expression` (Number-returning Expression)
    *   `seed0`, `seed1` (Constants)
    *   `basement_value`, `maximum_spot_basement_radius` (Number; constant)
    *   `region_size` (Number; constant; default = 512)
    *   `skip_offset`, `skip_span` (Number; constant)
    *   `hard_region_target_quantity` (Boolean; constant; default = true)
    *   `candidate_point_count`, `candidate_spot_count`, `suggested_minimum_candidate_point_spacing` (Number; constant; optional)
*   **Returns:** Number (Maximum height of any spot at a given point)

#### `multisample(expression, offset_x, offset_y)`
Evaluates the expression in a separate noise program with a larger grid.
*   **Parameters:**
    *   `expression` (Number-returning Expression)
    *   `offset_x`, `offset_y` (Number; constant 8-bit signed integer)
*   **Returns:** Number

### Voronoi Functions

#### `voronoi_spot_noise(...)`
The distance from the current location to the closest point.
*   **Parameters:**
    *   `x`, `y`, `seed0`, `seed1`
    *   `grid_size` (Number; constant 16-bit unsigned)
    *   `distance_type` (Number or String; enum): `chebyshev`, `manhattan`, `euclidean`, `minkowski3`
    *   `jitter` (Number; constant 0-1; default = 0.5)
*   **Returns:** Number

#### `voronoi_facet_noise(...)`
The distance from the 2nd closest point minus the distance to the closest point.
*   **Parameters:** Same as `voronoi_spot_noise`.
*   **Returns:** Number

#### `voronoi_pyramid_noise(...)`
Like facet noise but the gradient is uniform and represents the distance to the closest edge.
*   **Parameters:** Same as `voronoi_spot_noise` (distance_type excludes `minkowski3`).
*   **Returns:** Number

#### `voronoi_cell_id(...)`
A random value from 0 to 1 assigned per cell.
*   **Parameters:** Same as `voronoi_spot_noise`.
*   **Returns:** Number

### Control Flow & Utility

#### `if(condition, true_branch, false_branch)`
Copies a value from one of the branches depending on the condition. **Does not support short-circuit**; all branches are fully evaluated.
*   **Parameters:** `condition` (Boolean), `true_branch` (Number), `false_branch` (Number)
*   **Returns:** Number

#### `expression_in_range(...)`
Accepts between 5 and 254 positional arguments. Applies expressions based on ranges.
*   **Parameters (Positional):**
    *   `peak_multiplier`, `peak_maximum` (Number; constant)
    *   `expression` (1 to 84 instances)
    *   `range_from` (1 to 84 instances)
    *   `range_to` (1 to 84 instances)
*   **Returns:** Number

#### `distance_from_nearest_point(x, y, points, maximum_distance)`
Computes the euclidean distance of the position `{x, y}` from all positions listed in points.
*   **Parameters:**
    *   `x`, `y` (Number)
    *   `points` (MapPositionList)
    *   `maximum_distance` (Number; constant; default = infinity)
*   **Returns:** Number
*   **Example:**
    ```lua
    distance_from_nearest_point{x = x, y = y, points = starting_positions}
    ```

#### `distance_from_nearest_point_x(x, y, points)`
Returns the X coordinate of the closest point subtracted from current position.
*   **Returns:** Number

#### `distance_from_nearest_point_y(x, y, points)`
Returns the Y coordinate of the closest point subtracted from current position.
*   **Returns:** Number

#### `random_penalty(x, y, source, seed, amplitude)`
Subtracts a random value in the [0, amplitude) range from source if source is larger than 0.
*   **Parameters:**
    *   `x`, `y`, `source` (Number)
    *   `seed` (Number; constant; default = 1)
    *   `amplitude` (Number; constant; default = 1)
*   **Returns:** Number

#### `noise_layer_id(value)`
Returns the numeric value of the given string to be used as a noise layer ID.
*   **Parameters:** `value` (String)
*   **Returns:** Number

#### `var(value)`
Returns the variable specified by the given string. Useful for variable names with special characters.
*   **Parameters:** `value` (String)
*   **Returns:** Variable

#### `terrace(value, strength, offset, width)`
Terrace function.
*   **Parameters:** `value`, `strength`, `offset` (constant), `width` (constant)
*   **Returns:** Number

---

## 5. Performance Tips

Noise expressions are parsed into AST (abstract syntax tree) following operator precedence rules. Each AST node is treated separately.

### 5.1. Constant Folding
The compiler implements simple constant folding. If an AST node has all arguments constant, the expression result is computed directly.

**Optimization Examples:**
*   `1 + 2 + x` → `3 + x` (Folded)
*   `x + 1 + 2` → `(x + 1) + 2` (No folding)
*   `x ^ 2 ^ 3` → `x ^ 8` (Folded)
*   `2 * map_width * map_height * x` → `<constant> * x` (Folded at compile time)

**Arithmetic Identities:**
The compiler treats left-hand side expressions as identical to right-hand side expressions based on identities:
*   `x + 0` → `x`
*   `x * 1` → `x`
*   `x * 0` → `0`
*   `x ^ 0.5` → `sqrt(x)`
*   `x ^ 2` → `x * x`

### 5.2. Compile-time Deduplication
The compiler tries to deduplicate noise expressions which are **identical**. It does not recognize algebraic equivalence (e.g., `x + y` vs `y + x` are identical, but `x + y + z` vs `y + z + x` are not).

**Example Optimization:**
*   **Inefficient:** `10 * (x + y)`, `10 * (x - y)`, `10 * (-x + y)`, `20 + 10 * (-x - y)` (12 operations)
*   **Efficient:** `10 * x + 10 * y`, `10 * x - 10 * y`, `10 * y - 10 * x`, `20 - (10 * x + 10 * y)` (8 operations)

Enable verbose logging in-game to see estimated noise program complexity:
```text
Verbose CompiledMapGenSettings.cpp:355: "Entity noise program processed 4241 expressions (1813 unique) and has 1249 operations and 141 registers; estimated complexity: 23391."
```

### 5.3. Noise Seed, Scale and Offsets

**Seeds:**
*   `seed0` should always be `map_seed`.
*   `seed1` should be sequential or a string.
*   **Correct:** `seed0 = map_seed, seed1 = 0` or `seed1 = "my-named-noise"`
*   **Incorrect:** `seed0 = map_seed + 20`, `seed1 = 44869`

**Scaling:**
Use built-in `input_scale` and `output_scale` parameters instead of multiplying variables directly. This is significantly faster (around 5x for trivial cases).

*   **Correct:**
    ```lua
    basis_noise{x = x, y = y, input_scale = 2, output_scale = 1, seed0 = map_seed, seed1 = 0}
    ```
*   **Incorrect:**
    ```lua
    basis_noise{x = x * 2, y = y * 2, input_scale = 1, output_scale = 1, seed0 = map_seed, seed1 = 0}
    ```

**Multi-octave Noise:**
Use regular `multioctave_noise` instead of `quick_multioctave_noise` if possible. Their performance is very similar.