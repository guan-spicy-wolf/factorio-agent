# Factorio Auxiliary Documentation: Item Weight

**Version:** 2.0.76  
**Category:** Auxiliary Docs / Prototype Logic

## Overview

An item's **weight** is used to determine how many of it will fit on a rocket to supply a space platform. The weight can be set manually in the prototype definition or calculated automatically based on its recipe(s). This document describes the algorithm for automatic weight calculation.

## Calculation Algorithm

The game evaluates the following logic chain to determine an item's weight.

### 1. Flag Checks
*   If an item has **both** the `"only-in-cursor"` and `"spawnable"` flags, its weight is set to **`0`**.
*   If an item has **no recipe** to produce it, it falls back to the **`default_item_weight`**.

### 2. Recipe Weight Calculation
The game calculates the `'recipe weight'` of the item's selected recipe (see [Recipe Ordering](#recipe-ordering) for selection logic).

1.  Iterate over all ingredients:
    *   **Item Ingredients:** Increase weight by `item_weight * item_ingredient_count`.
        *   *Note:* This requires determining the weight of all ingredients first. If this results in a **recipe loop**, it falls back to the **`default_item_weight`** for that item.
    *   **Fluid Ingredients:** Increase weight by `fluid_ingredient_amount * 100`.
2.  If the resulting recipe weight is **`0`**, the item's weight falls back to the **`default_item_weight`**.

### 3. Product Count Calculation
The game determines the **product count** of the recipe:
1.  Iterate over all products.
2.  Add up the expected count (after probabilities) for all **item products**.
3.  **Fluid products** are skipped.
4.  If the recipe's product count is **`0`**, the item's weight falls back to the **`default_item_weight`**.

### 4. Intermediate Result
An intermediate result is calculated using the following formula:

```math
intermediate_result = (recipe_weight / product_count) * ingredient_to_weight_coefficient
```

*   **Default Coefficient:** `0.5` (Configurable via `ingredient_to_weight_coefficient`).

### 5. Final Weight Determination
The logic branches based on whether the recipe supports productivity.

#### Branch A: Recipe does NOT support productivity
1.  Calculate **Simple Result**:
    ```math
    simple_result = rocket_lift_weight / stack_size
    ```
2.  If `simple_result >= intermediate_result`:
    *   **Item Weight** = `simple_result`
3.  Otherwise, proceed to **Branch B** logic.

#### Branch B: Recipe supports productivity (or Simple Result was smaller)
1.  Calculate the amount of stacks resulting from the intermediate result:
    ```math
    stack_amount = rocket_lift_weight / intermediate_result / stack_size
    ```
2.  If `stack_amount <= 1`:
    *   **Item Weight** = `intermediate_result`
3.  Else (`stack_amount > 1`):
    *   **Item Weight** = `rocket_lift_weight / floor(stack_amount) / stack_size`

---

## Configuration Constants

The following global or prototype-specific values influence the calculation:

| Constant | Default Value | Description |
| :--- | :--- | :--- |
| `default_item_weight` | `100` | Fallback weight if calculation fails or no recipe exists. |
| `rocket_lift_weight` | `1000000` | Maximum weight capacity used for normalization. |
| `ingredient_to_weight_coefficient` | `0.5` | Multiplier applied during intermediate result calculation. |
| `stack_size` | varies | Defined in the item prototype. |

---

## Worked Example: Electronic Circuit

The following example demonstrates how the `electronic-circuit` item gets its weight.

**Configuration Context:**
```lua
default_item_weight = 100
rocket_lift_weight = 1000000
ingredient_to_weight_coefficient = 0.28 -- Specific to this item
```

**1. Recipe Selection**
The electronic circuit item has a recipe of the same name, which is used for calculation.

**2. Ingredient Weight Calculation**
```lua
ingredients = {
    { type = "item", name = "iron-plate",   amount = 1 }, -- Weight: 1000
    { type = "item", name = "copper-cable", amount = 3 }  -- Weight: 250
}

-- Recipe Weight = (1 * 1000) + (3 * 250) = 1750
```

**3. Product Count Calculation**
```lua
results = {
    { type = "item", name = "electronic-circuit", amount = 1 }
}

-- Product Count = 1
```

**4. Intermediate Result**
Since weight and count are > 0:
```math
intermediate_result = 1750 / 1 * 0.28 = 490
```

**5. Final Weight**
*   The recipe supports productivity, so the **Simple Result** branch is skipped.
*   Calculate stack count:
    ```math
    stack_amount = 1000000 / 490 / 200 = 10.2
    ```
*   Since `10.2 > 1`, the final calculation is:
    ```math
    final_weight = 1000000 / floor(10.2) / 200
    final_weight = 1000000 / 10 / 200 = 500
    ```

**Result:** The item weight is **500**.

---

## Recipe Ordering

Items can have multiple recipes to produce them. The game orders these recipes to determine which one is used for determining item weight.

**Exclusions:**
*   Recipes that are `hidden`.
*   Recipes that do not `allow_decomposition`.

**Sorting Priority:**
The sorting works by considering the following attributes in order, preferring recipes that fulfill them:

1.  **Name Match:** The name of the recipe is identical to the item name.
2.  **Catalyst:** The recipe is **not** using the item as a catalyst.
3.  **Hand Crafting:** The recipe can be used as an **intermediate** while hand-crafting.
4.  **Prototype Order:** The recipe's `category`, then `subgroup`, then `order`.