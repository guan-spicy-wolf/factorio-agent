"""Entity costs derived from the recipes in the data (plan-phase4c §2.B).

The cost of an item is the number of **raw units** it takes, recursively through the
recipes of the game data: a recipe's cost is the sum of its ingredients' costs, shared
over the amount of the item it makes; an item or fluid that no recipe in the data makes
(ores, wood, coal, petroleum gas, lubricant) is raw and costs 1 per unit. When several
recipes make an item, the cheapest counts. The unit ("one raw item") is a convention,
not a game number: costs only rank entities against each other.

An entity costs what the item of the same name costs (every entity the solvers place is
built from an item of its own name). Exact fractions: ``transport-belt`` is 1 iron
plate + 1 gear for 2 belts.
"""

from __future__ import annotations

from fractions import Fraction

from fgen.data.schema import GameData

RAW = Fraction(1)  # one raw unit (the convention above)


def _makers(gd: GameData) -> dict[str, list[tuple[str, Fraction]]]:
    """Item -> [(recipe, amount made per craft)]."""
    out: dict[str, list[tuple[str, Fraction]]] = {}
    for name, r in sorted(gd.recipes.items()):
        for item, amount in r.results:
            if amount > 0:
                out.setdefault(item, []).append((name, Fraction(amount).limit_denominator()))
    return out


def raw_cost(gd: GameData, item: str) -> Fraction:
    """Raw units per unit of ``item`` (see the module docstring)."""
    return costs(gd).get(item, RAW)  # in no recipe at all: raw


_CACHE: dict[int, dict[str, Fraction]] = {}


def costs(gd: GameData) -> dict[str, Fraction]:
    """Cost of every item a recipe of ``gd`` makes or uses (memoised per data set)."""
    key = id(gd)
    if key not in _CACHE:
        makers = _makers(gd)
        memo: dict[str, Fraction] = {}
        names = {n for r in gd.recipes.values() for n, _ in (*r.ingredients, *r.results)}
        for n in sorted(names):
            _cost(gd, n, makers, memo, ())
        _CACHE[key] = memo
    return _CACHE[key]


def _cost(
    gd: GameData,
    item: str,
    makers: dict[str, list[tuple[str, Fraction]]],
    memo: dict[str, Fraction],
    stack: tuple[str, ...],
) -> Fraction:
    if item in memo:
        return memo[item]
    best: Fraction | None = None
    for recipe, made in makers.get(item, []):
        r = gd.recipe(recipe)
        if any(n in stack or n == item for n, _ in r.ingredients):
            continue  # a cycle (e.g. a recycling recipe): not a way to make it
        total = sum(
            (
                Fraction(a).limit_denominator() * _cost(gd, n, makers, memo, (*stack, item))
                for n, a in r.ingredients
            ),
            Fraction(0),
        )
        c = total / made
        best = c if best is None else min(best, c)
    memo[item] = RAW if best is None else best
    return memo[item]


def entity_cost(gd: GameData, name: str) -> Fraction:
    """Raw units to build one ``name``; an entity no recipe makes is refused (a palette
    entity without a recipe in the seed: add it to ``scripts/extract_seed.py``)."""
    gd.entity(name)
    if name not in _makers(gd):
        raise ValueError(f"no recipe in the data makes {name!r}: its cost is unknown")
    return raw_cost(gd, name)
