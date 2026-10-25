"""Analytic throughput model (no simulation). Every number comes from ``GameData``.

The inserter estimate is deliberately coarse: one full arm revolution per item swing
(half a turn out, half back), hand size from the seed's engine constants, extension
time and belt pickup latency ignored. See VERIFY.md.
"""

from __future__ import annotations

import math
from typing import Any

from fgen.data.runtime import NO_RESEARCH, RuntimeState
from fgen.data.schema import GameData, parse_energy
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, inserter, mining
from fgen.layout.model import Unit

_EPS = 1e-9


def _check_machine(gd: GameData, recipe: str, machine: str) -> None:
    r, m = gd.recipe(recipe), gd.entity(machine)
    if m.crafting_speed is None:
        raise ValueError(f"{machine} is not a crafting machine")
    if not set(r.categories) & set(m.crafting_categories):
        raise ValueError(f"{machine} cannot craft {recipe} (categories {r.categories})")


def main_product(gd: GameData, recipe: str) -> tuple[str, float]:
    return gd.recipe(recipe).results[0]


def crafts_per_second(gd: GameData, recipe: str, machine: str) -> float:
    _check_machine(gd, recipe, machine)
    return gd.entity(machine).crafting_speed / gd.recipe(recipe).energy_required


def machine_rate(gd: GameData, recipe: str, machine: str) -> float:
    """Main product per second for one machine."""
    return crafts_per_second(gd, recipe, machine) * main_product(gd, recipe)[1]


def ingredient_rates(gd: GameData, recipe: str, machine: str) -> dict[str, float]:
    """Items per second consumed by one machine, per ingredient."""
    cps = crafts_per_second(gd, recipe, machine)
    return {name: amount * cps for name, amount in gd.recipe(recipe).ingredients}


def fuel_rate(gd: GameData, machine: str, fuel: str) -> float:
    """Fuel items per second for a burner machine running flat out (0 if not burner)."""
    e = gd.entity(machine)
    if not e.is_burner:
        return 0.0
    item = gd.items.get(fuel)
    if item is None or item.fuel_value is None:
        raise ValueError(f"{fuel} is not a fuel item")
    if e.energy_usage is None:
        raise ValueError(f"{machine} has no energy_usage")
    return parse_energy(e.energy_usage) / parse_energy(item.fuel_value)


def machines_needed(gd: GameData, recipe: str, machine: str, target_rate: float) -> int:
    if target_rate <= 0:
        raise ValueError("target_rate must be positive")
    return max(1, math.ceil(target_rate / machine_rate(gd, recipe, machine) - _EPS))


def belt_lane_capacity(gd: GameData, belt: str) -> float:
    return beltgeom.lane_items_per_second(gd, belt)


def inserter_hand_size(gd: GameData, name: str, rs: RuntimeState = NO_RESEARCH) -> float:
    """Items per swing: the engine's base hand size plus the force's research bonus
    (bulk inserters and the others have separate bonuses, ``RuntimeState``)."""
    e = gd.entity(name)
    if e.rotation_speed is None:
        raise ValueError(f"{name} is not an inserter")
    return gd.constant("inserter_base_hand_size") + rs.hand_bonus(e.bulk)


def inserter_throughput_estimate(gd: GameData, name: str, rs: RuntimeState = NO_RESEARCH) -> float:
    """Items per second, chest to chest: ``hand * tps * rotation_speed`` (one full arm
    revolution per swing), ``hand`` from :func:`inserter_hand_size`."""
    e = gd.entity(name)
    hand = inserter_hand_size(gd, name, rs)
    return hand * gd.constant("ticks_per_second") * float(e.rotation_speed or 0.0)


def drill_output_rate(
    gd: GameData, drill: str, resource: str, rs: RuntimeState = NO_RESEARCH
) -> float:
    """Products per second of one drill with the force's mining productivity bonus."""
    return mining.drill_rate(gd, drill, resource) * (1.0 + rs.mining_drill_productivity_bonus)


def _lane_count(gd: GameData, lane: str | None) -> float:
    return gd.constant("belt_lanes") if lane == "both" else 1.0


def predict(unit: Unit, gd: GameData, rs: RuntimeState = NO_RESEARCH) -> dict[str, Any]:
    """Area, machine count, theoretical throughput and bottleneck of a belt-fed row.

    Reads ``meta['recipe']``, ``meta['machine']`` and ``meta.get('fuel')``; everything
    else is measured on the layout itself (machines and inserters counted, belt tiers
    looked up at the interface tiles).
    """
    meta = unit.meta
    recipe, machine, fuel = meta["recipe"], meta["machine"], meta.get("fuel")
    layout = unit.layout
    w, h = layout.size(gd)
    machines = [p for p in layout.placements if p.name == machine]
    product, per_craft = main_product(gd, recipe)
    rate = machine_rate(gd, recipe, machine)
    ingr = {n: a / per_craft for n, a in gd.recipe(recipe).ingredients}  # per product
    fuel_pp = fuel_rate(gd, machine, fuel) / rate if fuel else 0.0

    at = layout.tile_index(gd)

    def belt_cap(tile: Any, lane: str | None) -> float:
        names = [layout.placements[i].name for i in at.get(tile, [])]
        belts = [n for n in names if gd.entity(n).type in beltgeom.BELT_TYPES]
        if not belts:
            return 0.0
        return belt_lane_capacity(gd, belts[0]) * _lane_count(gd, lane)

    limits: dict[str, float] = {"machines": len(machines) * rate}
    for ing, per_product in ingr.items():
        cap = sum(
            belt_cap(i.tile, i.lane)
            for i in unit.interfaces
            if i.role == "input" and i.kind == "item" and i.material == ing
        )
        limits[f"input:{ing}"] = cap / per_product
    if fuel_pp > 0:
        cap = sum(
            belt_cap(i.tile, i.lane)
            for i in unit.interfaces
            if i.role == "input" and i.kind == "item" and i.material == fuel
        )
        limits[f"fuel:{fuel}"] = cap / fuel_pp
    limits["output_belt"] = sum(
        belt_cap(i.tile, i.lane)
        for i in unit.interfaces
        if i.role == "output" and i.kind == "item" and i.material == product
    )

    # inserters: per machine, feeding capacity vs items needed per product
    items_pp = sum(ingr.values()) + fuel_pp
    ins_limit = 0.0
    machine_tiles = [p.tiles(gd) for p in machines]
    feed = [0.0] * len(machines)
    take = [0.0] * len(machines)
    for p in layout.placements:
        if gd.entity(p.name).type != "inserter":
            continue
        est = inserter_throughput_estimate(gd, p.name, rs)
        pu = inserter.pickup_tile(gd, p.name, p.position, p.direction)
        dr = inserter.drop_tile(gd, p.name, p.position, p.direction)
        for k, ts in enumerate(machine_tiles):
            if dr in ts:
                feed[k] += est
            if pu in ts:
                take[k] += est
    for k in range(len(machines)):
        ins_limit += min(feed[k] / items_pp if items_pp else math.inf, take[k], rate)
    limits["inserters"] = ins_limit

    bottleneck = min(limits, key=lambda k: limits[k])
    return {
        "area": w * h,
        "width": w,
        "height": h,
        "machines": len(machines),
        "machine_rate": rate,
        "limits": limits,
        "throughput": limits[bottleneck],
        "bottleneck": bottleneck,
        "target_rate": meta.get("target_rate"),
        "product": product,
        "footprint_machine": footprint.size(gd, machine),
        "runtime": rs.to_json(),
    }
