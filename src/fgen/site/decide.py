"""The judgment calls of a site, as questions an agent answers (plan-phase8 task A).

Two things in a site are preferences rather than computations: **which patch** of each
resource to mine, and **how placement trades belt length against turns, detours and
ore under a unit** (:class:`fgen.site.place.PlaceCosts`, plus the placement order's
priorities). This module turns each into a structured question — candidates, facts,
the default answer, what every knob means — and reads the answer back from a file.

Facts come only from the terrain snapshot, the contracts and the generators
(:func:`fgen.gen.site_sweep.choose_mining` run on every candidate at the demanded rate:
drills placed, belt rate, how long before a drill runs dry, what was dropped and why).
An answer names a candidate by its id and gives weights; it never holds a coordinate:
placement stays with the rule. Without an answer file every question takes its default,
which is exactly the behaviour before this module existed (nearest patch of at least
``min_tiles`` tiles, the default ``PlaceCosts``).

Answers are files under ``config/decisions/`` so the characterization rows that use
them are reproducible; ``fgen sweep site --decisions FILE`` reads one and suffixes the
case names with ``@<name>``.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from fgen.data.schema import GameData
from fgen.geom.vec import Tile
from fgen.site.build import ORDER_KEYS
from fgen.site.place import PlaceCosts
from fgen.terrain.model import Patch, Terrain, area_tiles, nearest_patch, patches

MIN_TILES = 50  # a candidate patch has at least this many tiles (as real_map_prepare)
NEAR = 45  # tiles around a patch's box counted for water / blocked / mixed ore (= margin)


def patch_id(p: Patch) -> str:
    lo, _ = p.bounds
    return f"{p.resource}@({lo.x},{lo.y})"


def candidates(terrain: Terrain, resource: str, min_tiles: int = MIN_TILES) -> list[Patch]:
    """Patches of ``resource`` with at least ``min_tiles`` tiles, nearest to the spawn
    first (the first one is the default answer)."""
    cands = [p for p in patches(terrain, resource) if len(p.tiles) >= min_tiles]
    return sorted(cands, key=lambda p: (_dist(p, (0.0, 0.0)), patch_id(p)))


def _dist(p: Patch, point: tuple[float, float]) -> float:
    return math.sqrt(
        min((t.x + 0.5 - point[0]) ** 2 + (t.y + 0.5 - point[1]) ** 2 for t in p.tiles)
    )


def _between(a: Patch, b: Patch) -> float:
    """Shortest tile distance between two patches' boxes (0 when they touch)."""
    (alo, ahi), (blo, bhi) = a.bounds, b.bounds
    dx = max(0, blo.x - ahi.x, alo.x - bhi.x)
    dy = max(0, blo.y - ahi.y, alo.y - bhi.y)
    return float(dx + dy)


def cleared(terrain: Terrain) -> Terrain:
    """The terrain as it will be after clearing obstacles (trees, rocks, cliffs go; water
    and the map edge stay): what the mining generator sees on a real-map run."""
    return Terrain(terrain.buildable, terrain.resources, terrain.water, frozenset())


def patch_facts(
    gd: GameData,
    terrain: Terrain,
    patch: Patch,
    demand: float,
    window_s: float,
    *,
    others: dict[str, list[Patch]] | None = None,
    near: int = NEAR,
) -> dict[str, Any]:
    """Everything an agent may weigh about one candidate patch: size, amount, place,
    distances, mixed ore, water and blocked tiles around it, and what the mining
    generator makes of it at ``demand`` items/s (one belt, as the site would)."""
    from fgen.gen.mining_patch import generate as gen_mining
    from fgen.gen.site_sweep import choose_mining

    lo, hi = patch.bounds
    box = (Tile(lo.x - near, lo.y - near), Tile(hi.x + near, hi.y + near))
    around = area_tiles(box)
    water = sum(1 for t in around if t in terrain.water)
    blocked = sum(1 for t in around if t not in terrain.buildable and t not in terrain.water)
    mixed = sum(
        1
        for t in area_tiles((Tile(lo.x - 2, lo.y - 2), Tile(hi.x + 2, hi.y + 2)))
        if (r := terrain.resource_at(t)) is not None and r[0] != patch.resource
    )
    cx, cy = patch.centroid()
    facts: dict[str, Any] = {
        "id": patch_id(patch),
        "resource": patch.resource,
        "tiles": len(patch.tiles),
        "total_amount": patch.total_amount,
        "bounds": [[lo.x, lo.y], [hi.x, hi.y]],
        "size": list(patch.size),
        "centroid": [round(cx, 1), round(cy, 1)],
        "distance_to_spawn": round(_dist(patch, (0.0, 0.0)), 1),
        "mixed_ore_tiles_at_edge": mixed,
        f"water_tiles_within_{near}": water,
        f"blocked_tiles_within_{near}": blocked,
        "distance_to_other_candidates": {
            r: [[patch_id(q), _between(patch, q)] for q in qs]
            for r, qs in (others or {}).items()
            if r != patch.resource
        },
    }
    try:
        mp = choose_mining(gd, cleared(terrain), patch, max_rate=demand, window_s=window_s)
        u = gen_mining(mp, gd, patch, cleared(terrain))
        a = u.meta["anchor"]
        facts["mining_at_demand"] = {
            "demand": demand,
            "drills": a["drills"],
            "belt_rate": a["rate"],
            "first_dry_s_lower_bound": a["first_dry_s_lower_bound"],
            "est_depletion_s": a["est_depletion_s"],
            "dropped_by_reason": u.meta.get("dropped_by_reason"),
            "params": {"axis": mp.axis, "flow": mp.flow, "min_ore": mp.min_ore_amount_per_drill},
        }
    except ValueError as exc:
        facts["mining_at_demand"] = {"demand": demand, "error": str(exc)}
    return facts


def cost_docs() -> dict[str, str]:
    return {
        "step": "per tile of estimated belt between a unit's port and its neighbour's",
        "turn": "per 90-degree turn the two ports' directions force on the belt",
        "detour": "per tile of overshoot when the goal lies behind the start",
        "resource": "per footprint tile a free unit would put on an ore tile",
        "slack": "placement window radius beyond the unit's longer side (tiles); "
        "doubled once when nothing fits",
    }


def questions(
    gd: GameData,
    terrain: Terrain,
    demand: dict[str, float],
    window_s: float,
    *,
    min_tiles: int = MIN_TILES,
    product: str | None = None,
) -> dict[str, Any]:
    """The site's questions: one per resource in ``demand`` (items/s wanted from it,
    from the station's contracts) with its candidates' facts, and the placement
    preferences. The first candidate is the default (nearest)."""
    cands = {r: candidates(terrain, r, min_tiles) for r in sorted(demand)}
    out: dict[str, Any] = {
        "product": product,
        "window_s": window_s,
        "patches": {},
        "place_costs": {
            "default": asdict(PlaceCosts()),
            "meaning": cost_docs(),
        },
        "order_priority": {
            "default": list(ORDER_KEYS),
            "meaning": "placement order among the units whose producers are placed: "
            "generated units before fixtures, then most placed neighbours, then the "
            "largest linked rate; a permutation of these keys",
        },
    }
    for r, ps in cands.items():
        out["patches"][r] = {
            "demand": demand[r],
            "default": patch_id(ps[0]) if ps else None,
            "candidates": [
                patch_facts(gd, terrain, p, demand[r], window_s, others=cands) for p in ps
            ],
        }
    return out


@dataclass
class Decisions:
    """An answer file: which candidate per resource (by id; a resource left out takes
    the default), the placement weights, the order priorities, and who answered."""

    name: str = "default"
    patches: dict[str, str] = field(default_factory=dict)
    place_costs: PlaceCosts = field(default_factory=PlaceCosts)
    order_priority: tuple[str, ...] = ORDER_KEYS
    answered_by: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    @classmethod
    def load(cls, path: str | Path) -> Decisions:
        d = json.loads(Path(path).read_text())
        costs = d.get("place_costs") or {}
        known = {f.name for f in fields(PlaceCosts)}
        bad = set(costs) - known
        if bad:
            raise ValueError(f"{path}: unknown place_costs {sorted(bad)}")
        order = tuple(d.get("order_priority") or ORDER_KEYS)
        if sorted(order) != sorted(ORDER_KEYS):
            raise ValueError(f"{path}: order_priority must permute {ORDER_KEYS}")
        for v in (d.get("patches") or {}).values():
            if not isinstance(v, str) or "@" not in v:
                raise ValueError(f"{path}: a patch answer is a candidate id, got {v!r}")
        return cls(
            name=d.get("name") or Path(path).stem,
            patches=dict(d.get("patches") or {}),
            place_costs=PlaceCosts(**costs),
            order_priority=order,
            answered_by=dict(d.get("answered_by") or {}),
            rationale=str(d.get("rationale") or ""),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "patches": dict(self.patches),
            "place_costs": asdict(self.place_costs),
            "order_priority": list(self.order_priority),
            "answered_by": dict(self.answered_by),
            "rationale": self.rationale,
        }

    def choose(self, terrain: Terrain, resource: str, min_tiles: int = MIN_TILES) -> Patch | None:
        """The answered candidate of ``resource`` (by id), else the default."""
        want = self.patches.get(resource)
        if want is None:
            return nearest_patch(terrain, resource, (0.0, 0.0), min_tiles=min_tiles)
        for p in candidates(terrain, resource, min_tiles):
            if patch_id(p) == want:
                return p
        raise ValueError(f"{self.name}: no candidate {want!r} for {resource} in this snapshot")


DEFAULT = Decisions()

__all__ = [
    "DEFAULT",
    "Decisions",
    "candidates",
    "patch_facts",
    "patch_id",
    "questions",
]
