"""Objective presets for the period solver (plan-phase4c §2.B).

The hard constraints (rates, ratios, lane capacities) never change; a preset only says
how feasible periods are ranked. Each preset is a **lexicographic list of terms**:

- ``compact``: area, then poles, then inserters (the phase-4a/4b objective), then the
  total entity cost as the tie-break (the 4c record: with the objective alone, a period
  whose area, poles and inserters are already fixed took the dearest belt and pole
  tiers of the palette; the cost term only ranks those ties, it never trades a tile,
  a pole or an inserter for a cheaper entity);
- ``cheap``: total entity cost (:mod:`fgen.data.cost`, raw units through the recipes),
  then the compact terms;
- ``simple``: fewest distinct entity kinds, then the compact terms.

The tie-break is part of the key, so it is applied wherever the key is: the anytime
driver's models and its final polish (:mod:`fgen.solve.cpsat`), and the ranking of
every driver's candidates. Two places keep ties unbroken by the model itself: the
proof driver (``prove=True``, the phase-4b objective the characterization rows were
solved under; its candidates are still ranked by the key) and the exhaustive backend
(:mod:`fgen.solve.cycle`), which takes one tier of each type, so its cost is a function
of the entity count it already ranks by.

Terms (all over the period's non-machine entities; the machines are fixed by the spec):
``area`` = width x height; ``poles``; ``inserters`` = inserters + pipes (the 4b/5a
count); ``cost`` in units of ``1 / scale`` raw units (``scale`` = the lcm of the
palette's cost denominators, so the integer is exact); ``kinds`` = distinct entity
names. A preset becomes one integer key ``sum term_i * prod_{j > i} (ub_j + 1)``, the
same on the model side (:mod:`fgen.solve.cpsat`) and here, so periods of different
sizes compare by one number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from fgen.data import cost as costmod
from fgen.data.schema import GameData

PRESETS: dict[str, tuple[str, ...]] = {
    "compact": ("area", "poles", "inserters", "cost"),
    "cheap": ("cost", "area", "poles", "inserters"),
    "simple": ("kinds", "area", "poles", "inserters"),
}
MACHINE_TYPES = frozenset({"assembling-machine", "furnace"})


@dataclass(frozen=True)
class Scale:
    """Integer cost per palette entity and the upper bound of every term."""

    cost: dict[str, int]  # entity -> cost * scale
    scale: int
    ub: dict[str, int]

    def key(self, preset: str, values: dict[str, int]) -> int:
        out = 0
        for t in PRESETS[preset]:
            out = out * (self.ub[t] + 1) + values[t]
        return out

    def weights(self, preset: str) -> dict[str, int]:
        """Coefficient of each term in :meth:`key`."""
        terms = PRESETS[preset]
        w, out = 1, {}
        for t in reversed(terms):
            out[t] = w
            w *= self.ub[t] + 1
        return out


def scale_for(gd: GameData, names: list[str], max_width: int, max_height: int) -> Scale:
    """Costs of ``names`` (the palette and the pipe entities) and term bounds for periods
    up to ``max_width x max_height``."""
    fr = {n: costmod.entity_cost(gd, n) for n in names}
    scale = math.lcm(*(c.denominator for c in fr.values())) if fr else 1
    cost = {n: int(c * scale) for n, c in fr.items()}
    tiles = max_width * max_height
    ub = {
        "area": tiles,
        "poles": tiles,
        "inserters": tiles,
        "cost": tiles * max(cost.values(), default=0),
        "kinds": len(names),
    }
    return Scale(cost, scale, ub)


def metrics(per: Any, gd: GameData, sc: Scale) -> dict[str, Any]:
    """The terms of a solved :class:`~fgen.solve.period.Period`."""
    others = [p for p in per.placements if gd.entity(p.name).type not in MACHINE_TYPES]
    types = [gd.entity(p.name).type for p in others]
    c = sum(sc.cost[p.name] for p in others)
    return {
        "area": per.area,
        "poles": sum(1 for t in types if t == "electric-pole"),
        "inserters": sum(1 for t in types if t in ("inserter", "pipe", "pipe-to-ground")),
        "cost": c,
        "kinds": len({p.name for p in others}),
        "cost_units": round(c / sc.scale, 6),  # raw units (for reports; not a term)
    }
