"""Phase 4a: period model, boundary conditions and the exhaustive period solver."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from fgen.gen import throughput as tp
from fgen.gen.direct_chain_baseline import baseline_period
from fgen.geom import inserter
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.validate import validate
from fgen.solve.cycle import solve
from fgen.solve.period import CycleSpec, Period, Stage, check_period, flows, ratio, tile

AM1 = "assembling-machine-1"


def spec(a: int = 3, b: int = 2, machine: str = AM1, **kw) -> CycleSpec:
    return CycleSpec(
        (Stage("electronic-circuit", machine, b), Stage("copper-cable", machine, a)), **kw
    )


def tiled_violations(per: Period, n: int, gd) -> list:
    """validate on n copies; belt rows end at the chain's east edge (the generator
    declares those as the output port and input terminals)."""
    ends = [Tile(n * per.width - 1, b.y) for b in per.belts]
    return validate(tile(per, n), gd, belt_terminals=ends)


def test_ratio_from_recipes(gd):
    q = ratio(gd, ("electronic-circuit", AM1), ("copper-cable", AM1))
    cable = dict(gd.recipe("electronic-circuit").ingredients)["copper-cable"]
    made = gd.recipe("copper-cable").results[0][1]
    assert q == Fraction(cable / made).limit_denominator()
    # the ratio does not depend on the machine tier as long as both stages share it
    assert (
        ratio(
            gd,
            ("electronic-circuit", "assembling-machine-2"),
            ("copper-cable", "assembling-machine-2"),
        )
        == q
    )


def test_flows_exact_and_approximate(gd):
    f = flows(spec(), gd)
    rate = tp.machine_rate(gd, "electronic-circuit", AM1)
    assert f.inputs == ("copper-plate", "iron-plate") and f.outputs == ("electronic-circuit",)
    assert f.output_rate["electronic-circuit"] == pytest.approx(2 * rate)
    assert f.surplus["copper-cable"] == pytest.approx(0)
    f2 = flows(spec(2, 1), gd)
    assert f2.surplus["copper-cable"] > 0  # 2:1 makes more cable than it uses
    # copper is only consumed as fast as cable is used
    per_cable = 1 / gd.recipe("copper-cable").results[0][1]
    need = dict(gd.recipe("electronic-circuit").ingredients)["copper-cable"] * rate
    assert f2.input_rate["copper-plate"] == pytest.approx(need * per_cable)
    with pytest.raises(ValueError):
        flows(spec(1, 2), gd)  # too few producers


def test_handmade_period_tiles_cleanly(gd):
    """The hand-written classic period meets the boundary conditions; 4 and 8 copies
    pass every invariant."""
    per = baseline_period(spec(), gd)
    assert check_period(per, gd) == []
    for n in (1, 2, 4, 8):
        assert tiled_violations(per, n, gd) == [], n


def test_boundary_condition_checks_catch_breaks(gd):
    per = baseline_period(spec(), gd)
    poles = [i for i, p in enumerate(per.placements) if gd.entity(p.name).type == "electric-pole"]
    # drop the last pole: something loses supply or the copies no longer join
    cut = replace(per, placements=tuple(p for i, p in enumerate(per.placements) if i != poles[-1]))
    assert check_period(cut, gd)
    # a gap in a belt row
    belt_i = next(i for i, p in enumerate(per.placements) if p.name == per.spec.belt)
    gap = replace(per, placements=tuple(p for i, p in enumerate(per.placements) if i != belt_i))
    assert any("belt row" in s for s in check_period(gap, gd))
    # an inserter whose pickup falls outside the period
    ins = next(p for p in per.placements if p.name == per.spec.inserter)
    out = replace(ins, position=Vec2(0.5, ins.position.y), direction=Direction.W)
    bad = replace(per, placements=(*per.placements, out))
    assert inserter.pickup_tile(gd, out.name, out.position, out.direction).x < 0
    assert any("outside the period" in s for s in check_period(bad, gd))
    # a period too narrow for its poles to reach the next copy
    lone = next(per.placements[i] for i in poles)
    one = replace(
        per,
        width=per.width * 4,
        placements=(
            *(p for p in per.placements if gd.entity(p.name).type != "electric-pole"),
            lone,
        ),
    )
    assert any("next period" in s for s in check_period(one, gd))


def test_solver_finds_32_circuit_period(gd):
    res = solve(spec(), gd, time_limit=60)
    st = res.stats
    assert not st.timed_out and st.seconds < 60
    per = res.best
    assert per is not None
    assert check_period(per, gd) == []
    for n in (4, 8):
        assert tiled_violations(per, n, gd) == [], n
    # nothing smaller exists in the search domain, and the hand-written one is no smaller
    base = baseline_period(spec(), gd)
    assert per.area <= base.area
    assert all(s["found"] == 0 for s in st.sizes if s["width"] * s["height"] < per.area)
    # explainable: counts per level and per constraint
    assert st.nodes["machine"] > 0 and st.nodes["pole"] > 0
    for key in ("belt_contact", "overlap", "dangling_producer"):
        assert st.pruned[key] > 0
    machines = [p for p in per.placements if gd.entity(p.name).type == "assembling-machine"]
    assert (
        sorted(p.extra["recipe"] for p in machines)
        == ["copper-cable"] * 3 + ["electronic-circuit"] * 2
    )
    # the intermediate never touches a belt: only raw materials in, product out
    assert {m for b in per.belts if b.role == "input" for m in b.materials()} == {
        "copper-plate",
        "iron-plate",
    }
    assert {m for b in per.belts if b.role == "output" for m in b.materials()} == {
        "electronic-circuit"
    }


@pytest.mark.parametrize(
    "kw",
    [
        {"machine": "assembling-machine-2"},
        {"pole": "medium-electric-pole"},
        {"inserter": "inserter"},
    ],
    ids=["am2", "medium-pole", "basic-inserter"],
)
def test_solver_tiers_are_parameters(gd, kw):
    machine = kw.pop("machine", AM1)
    res = solve(spec(machine=machine, **kw), gd, time_limit=60)
    assert res.best is not None and not res.stats.timed_out
    for n in (4, 8):
        assert tiled_violations(res.best, n, gd) == []


def test_solver_21_period_and_bounds(gd):
    res = solve(spec(2, 1), gd)
    assert res.best is not None
    assert tiled_violations(res.best, 4, gd) == []
    assert res.best.area < solve(spec(), gd).best.area
    # a search box too small for any period: no result, but the search finished
    small = solve(spec(), gd, max_width=12, max_height=8)
    assert small.best is None and not small.stats.timed_out


def test_lane_capacity_for_repeats(gd):
    """With enough periods on one belt the shared input belt overflows its copper lane.
    A belt of copper's own cannot work with 1-tile inserters (the cable machines would
    sit across a belt from the circuit machines), so the search proves there is no
    period; a faster belt has the lane capacity again."""
    f = flows(spec(), gd)
    lane = tp.belt_lane_capacity(gd, "transport-belt")
    k = int(lane // f.input_rate["copper-plate"]) + 1
    res = solve(spec(), gd, repeats=k)
    assert res.best is None and not res.stats.timed_out
    assert res.stats.pruned["lane_capacity"] > 0 and res.stats.pruned["no_link_rows"] > 0
    fast = solve(spec(belt="fast-transport-belt"), gd, repeats=k)
    assert fast.best is not None
    assert tiled_violations(fast.best, k, gd) == []


def test_mixed_tiers_beat_the_classic_row(gd):
    """Allowing long-handed inserters opens two-row periods: smaller than the classic
    row (which needs every consumer between a raw-material belt and the output belt)."""
    mixed = spec(extra_inserters=("long-handed-inserter",))
    res = solve(mixed, gd, time_limit=60)
    assert res.best is not None and not res.stats.timed_out
    assert res.best.area < baseline_period(spec(), gd).area
    names = {p.name for p in res.best.placements if gd.entity(p.name).type == "inserter"}
    assert names <= set(mixed.inserters)
    for n in (4, 8):
        assert tiled_violations(res.best, n, gd) == []
