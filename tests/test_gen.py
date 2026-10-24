import itertools
import math
from dataclasses import replace

import pytest

from fgen.data.schema import GameData, parse_energy
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.gen.smelting_row import SmeltingRow, SmeltingRowParams, generate
from fgen.geom import belt, footprint
from fgen.layout import transform as tf
from fgen.layout.validate import validate_unit
from fgen.sim import protocol

# ---- throughput ----------------------------------------------------------------


def test_machine_rate_from_seed(gd: GameData) -> None:
    r = gd.recipe("iron-plate")
    for f in ["stone-furnace", "steel-furnace", "electric-furnace"]:
        expected = gd.entity(f).crafting_speed * r.results[0][1] / r.energy_required
        assert tp.machine_rate(gd, "iron-plate", f) == pytest.approx(expected)
    with pytest.raises(ValueError):
        tp.machine_rate(gd, "iron-gear-wheel", "stone-furnace")  # wrong category


def test_machines_needed(gd: GameData) -> None:
    rate = tp.machine_rate(gd, "iron-plate", "stone-furnace")
    for target in [0.1, 5, 7.5, 15, 30]:
        n = tp.machines_needed(gd, "iron-plate", "stone-furnace", target)
        assert n * rate >= target - 1e-9
        assert (n - 1) * rate < target
    # exact multiples do not round up
    assert tp.machines_needed(gd, "iron-plate", "stone-furnace", 3 * rate) == 3


def test_fuel_and_lane(gd: GameData) -> None:
    usage = parse_energy(gd.entity("stone-furnace").energy_usage)
    fuel = parse_energy(gd.items["coal"].fuel_value)
    assert tp.fuel_rate(gd, "stone-furnace", "coal") == pytest.approx(usage / fuel)
    assert tp.fuel_rate(gd, "electric-furnace", "coal") == 0
    assert tp.belt_lane_capacity(gd, "transport-belt") == belt.lane_items_per_second(
        gd, "transport-belt"
    )


def test_inserter_estimate(gd: GameData) -> None:
    for n in ["inserter", "fast-inserter", "long-handed-inserter"]:
        e = gd.entity(n)
        expected = (
            gd.constants["inserter_base_hand_size"]
            * gd.constants["ticks_per_second"]
            * e.rotation_speed
        )
        assert tp.inserter_throughput_estimate(gd, n) == pytest.approx(expected)
    assert tp.inserter_throughput_estimate(gd, "fast-inserter") > tp.inserter_throughput_estimate(
        gd, "inserter"
    )


# ---- smelting row ------------------------------------------------------------------


def _fast_enough_belt(gd: GameData, p: SmeltingRowParams) -> SmeltingRowParams:
    """Cheapest belt tier whose single lane carries the target (derived, not hard-coded)."""
    tiers = sorted(
        (n for n, e in gd.entities.items() if e.type == "transport-belt"),
        key=lambda n: belt.items_per_second(gd, n),
    )
    for b in tiers:
        if tp.belt_lane_capacity(gd, b) >= p.target_rate:
            return replace(p, belt=b)
    raise AssertionError("no belt fast enough")


@pytest.mark.parametrize("rate", [5, 7.5, 15])
def test_acceptance_rates(gd: GameData, rate: float) -> None:
    p = _fast_enough_belt(gd, SmeltingRowParams(target_rate=rate))
    unit = generate(p, gd)
    assert validate_unit(unit, gd) == []
    pred = tp.predict(unit, gd)
    assert pred["throughput"] >= rate - 1e-9
    assert pred["machines"] == tp.machines_needed(gd, p.recipe, p.furnace, rate)
    fw, _ = footprint.size(gd, p.furnace)
    assert unit.layout.size(gd)[0] == pred["machines"] * fw


def test_default_rate_15_needs_param_change(gd: GameData) -> None:
    lane = tp.belt_lane_capacity(gd, "transport-belt")
    p = SmeltingRowParams(target_rate=2 * lane)
    with pytest.raises(Infeasible) as ei:
        generate(p, gd)
    assert ei.value.suggestions
    assert len(ei.value.changes) == len(ei.value.suggestions)
    for change in ei.value.changes:
        unit = generate(replace(p, **change), gd)
        assert validate_unit(unit, gd) == []
        assert tp.predict(unit, gd)["throughput"] >= min(p.target_rate, unit.meta["target_rate"])


def test_infeasible_30_yellow_single_stone(gd: GameData) -> None:
    p = SmeltingRowParams(target_rate=30, belt="transport-belt", sides=1)
    with pytest.raises(Infeasible) as ei:
        generate(p, gd)
    assert "lane" in ei.value.reason
    assert ei.value.suggestions
    assert any(s.startswith("target_rate=") for s in ei.value.suggestions)


@pytest.mark.parametrize("pole", ["small-electric-pole", "medium-electric-pole"])
@pytest.mark.parametrize("furnace", ["stone-furnace", "steel-furnace", "electric-furnace"])
def test_swap_pole_and_furnace_without_code_change(gd: GameData, pole: str, furnace: str) -> None:
    unit = generate(SmeltingRowParams(furnace=furnace, pole=pole), gd)
    assert validate_unit(unit, gd) == []
    assert tp.predict(unit, gd)["throughput"] >= 7.5 - 1e-9


def _pole_rows(gd: GameData, unit) -> set[int]:
    return {
        p.position.floor().y
        for p in unit.layout.placements
        if gd.entity(p.name).type == "electric-pole"
    }


def test_pole_rows_derived_from_supply_distance(gd: GameData) -> None:
    """One pole row iff a pole in a service band reaches the other band's inserters."""
    for pole in ["small-electric-pole", "medium-electric-pole"]:
        unit = generate(SmeltingRowParams(pole=pole), gd)
        ins_rows = sorted(
            {
                p.position.floor().y
                for p in unit.layout.placements
                if gd.entity(p.name).type == "inserter"
            }
        )
        gap = ins_rows[-1] - ins_rows[0]
        sad = gd.entity(pole).supply_area_distance
        reach_below = math.floor(0.5 + sad) - 1  # tiles below the pole's own row
        expected_rows = 1 if reach_below >= gap else 2
        assert len(_pole_rows(gd, unit)) == expected_rows, pole


def test_pole_spacing_respects_wire_distance(gd: GameData) -> None:
    for pole in ["small-electric-pole", "medium-electric-pole"]:
        unit = generate(SmeltingRowParams(pole=pole), gd)
        wire = gd.entity(pole).maximum_wire_distance
        for y in _pole_rows(gd, unit):
            xs = sorted(
                p.position.x
                for p in unit.layout.placements
                if p.name == pole and p.position.floor().y == y
            )
            gaps = [b - a for a, b in itertools.pairwise(xs)]
            assert all(g <= wire for g in gaps)


def test_sides_2(gd: GameData) -> None:
    unit = generate(SmeltingRowParams(sides=2, target_rate=5), gd)
    assert validate_unit(unit, gd) == []
    outs = [i for i in unit.interfaces if i.role == "output"]
    assert len(outs) == 2 and {i.lane for i in outs} == {"left", "right"}
    assert sum(i.rate for i in outs) >= 5


def test_interfaces_contract(gd: GameData) -> None:
    unit = generate(SmeltingRowParams(), gd)
    kinds = sorted((i.kind, i.role) for i in unit.interfaces)
    assert ("power", "input") in kinds and ("item", "output") in kinds
    ore = next(i for i in unit.interfaces if i.material == "iron-ore")
    lo, hi = unit.layout.bounds(gd)
    assert ore.tile.x == lo.x and ore.lane == "left" and ore.assumptions
    out = next(i for i in unit.interfaces if i.role == "output")
    assert out.tile.x == hi.x


def test_unit_survives_transforms(gd: GameData) -> None:
    from fgen.geom.vec import Direction

    for p in [SmeltingRowParams(), SmeltingRowParams(sides=2, target_rate=5)]:
        unit = generate(p, gd)
        for t in [
            lambda u: tf.rotate(u, Direction.E),
            lambda u: tf.rotate(u, Direction.S),
            lambda u: tf.rotate(u, Direction.W),
            tf.mirror_x,
            tf.mirror_y,
            lambda u: tf.translate(u, 17, -4),
        ]:
            assert validate_unit(t(unit), gd) == []


def test_bad_names(gd: GameData) -> None:
    with pytest.raises(Infeasible) as ei:
        generate(SmeltingRowParams(furnace="assembling-machine-1"), gd)
    assert any("stone-furnace" in s for s in ei.value.suggestions)
    with pytest.raises(Infeasible):
        generate(SmeltingRowParams(recipe="iron-gear-wheel"), gd)
    with pytest.raises(Infeasible):
        generate(SmeltingRowParams(inserter="burner-inserter"), gd)
    with pytest.raises(Infeasible) as ei:
        generate(SmeltingRowParams(pole="substation"), gd)
    assert ei.value.suggestions
    with pytest.raises(Infeasible):
        generate(SmeltingRowParams(furnace="stone-furnace", fuel_lane=False), gd)


def test_generator_protocol(gd: GameData) -> None:
    g = SmeltingRow()
    assert g.family.startswith("belt-fed-row")
    assert g.invariants(g.generate(SmeltingRowParams(target_rate=1), gd), gd) == []


def test_sim_not_available() -> None:
    from fgen.geom.vec import Tile

    with pytest.raises(RuntimeError, match="headless"):
        protocol.NotAvailableDeployer().place(None, Tile(0, 0))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        protocol.NotAvailableTerrain().buildable(Tile(0, 0))
    with pytest.raises(RuntimeError):
        protocol.NotAvailableCharacterizer().measure(None)  # type: ignore[arg-type]
