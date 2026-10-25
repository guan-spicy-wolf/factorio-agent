"""Phase 4b task C: science packs on the direct_chain family (recipe trees, surplus,
intermediates on a belt lane). CP-SAT cases skip without OR-Tools."""

from __future__ import annotations

from fractions import Fraction

import pytest

from fgen.gen import direct_chain as dc
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.solve import cpsat
from fgen.solve.period import CycleSpec, Stage, belt_sets, flows

RED = "automation-science-pack"
GREEN = "logistic-science-pack"
AM1 = "assembling-machine-1"
needs_ortools = pytest.mark.skipif(not cpsat.available(), reason=cpsat.INSTALL_HINT)


def test_trees_and_exact_counts(gd):
    red = dc.DirectChainParams(recipe=RED)
    assert [i for i, _ in dc.tree(red, gd)] == ["iron-gear-wheel"]
    q = dc.exact_counts(red, gd)["iron-gear-wheel"]
    need = tp.ingredient_rates(gd, RED, AM1)["iron-gear-wheel"]
    assert q == Fraction(need / tp.machine_rate(gd, "iron-gear-wheel", AM1)).limit_denominator()
    green = dc.DirectChainParams(recipe=GREEN, belt_inputs=("electronic-circuit",))
    items = {i for i, _ in dc.tree(green, gd)}
    assert items == {"inserter", "transport-belt", "iron-gear-wheel"}
    # without belt_inputs the circuit (and its cable) would be made in the chain too
    assert "electronic-circuit" in {i for i, _ in dc.tree(dc.DirectChainParams(recipe=GREEN), gd)}


def test_flows_propagate_shares_down_the_tree(gd):
    spec = CycleSpec(
        (
            Stage(GREEN, AM1, 1),
            Stage("inserter", AM1, 1),
            Stage("transport-belt", AM1, 1),
            Stage("iron-gear-wheel", AM1, 1),
        )
    )
    f = flows(spec, gd)
    pack = tp.machine_rate(gd, GREEN, AM1)
    # gears only as fast as the (partly idle) inserter and belt machines use them
    ins_gear = dict(gd.recipe("inserter").ingredients)["iron-gear-wheel"]
    belt_gear = dict(gd.recipe("transport-belt").ingredients)["iron-gear-wheel"]
    belts_per = gd.recipe("transport-belt").results[0][1]
    assert f.internal["iron-gear-wheel"][1] == pytest.approx(
        pack * ins_gear + pack / belts_per * belt_gear
    )
    assert f.input_rate["electronic-circuit"] == pytest.approx(pack)


def test_plans_prefer_fewer_machines_lanes_on_ties(gd):
    p = dc.DirectChainParams(recipe=RED, target_rate=1.0, backend="cpsat")
    first = dc.plans(p, gd)[0]
    # one gear machine serves the whole exact ratio only through a belt lane
    assert first.spec.lane_items == ("iron-gear-wheel",) and first.periods == 1
    direct = dc.plans(p.__class__(**{**p.__dict__, "intermediates": "direct"}), gd)
    assert all(not pl.spec.lane_items for pl in direct)
    assert sum(s.count for s in first.spec.stages) < min(
        pl.periods * sum(s.count for s in pl.spec.stages) for pl in direct
    )


def test_lane_skeletons_fit_the_lanes(gd):
    spec = CycleSpec(
        (Stage(RED, AM1, 5), Stage("iron-gear-wheel", AM1, 1)), lane_items=("iron-gear-wheel",)
    )
    for belts in belt_sets(flows(spec, gd), int(gd.constant("belt_lanes")), spec.lane_items):
        for b in belts:
            assert len(b.materials) <= int(gd.constant("belt_lanes"))


def test_exhaustive_has_no_lanes(gd):
    p = dc.DirectChainParams(
        recipe=RED, target_rate=1.0, intermediates="lane", backend="exhaustive"
    )
    with pytest.raises(Infeasible) as exc:
        dc.generate(p, gd)
    assert "cpsat" in exc.value.reason or "cpsat" in " ".join(exc.value.suggestions)


@needs_ortools
def test_red_direct_surplus_period_both_backends(gd):
    """1 gear machine : 2 red machines, the most direct insertion reaches (1:3 and up
    are proved infeasible by CP-SAT); both backends give the same smallest area."""
    p = dc.DirectChainParams(
        recipe=RED, target_rate=0.5, intermediates="direct", max_consumers=2, backend="cpsat"
    )
    a = dc.generate(p, gd)
    b = dc.generate(dc.DirectChainParams(**{**p.__dict__, "backend": "exhaustive"}), gd)
    assert a.meta["ratio"] == b.meta["ratio"] == [1, 2]
    assert a.meta["period"]["area"] == b.meta["period"]["area"]
    assert dc.predict(a, gd)["throughput"] >= p.target_rate
    notes = " ".join(n for i in a.interfaces for n in i.assumptions)
    assert "iron-gear-wheel" in notes and "idle" in notes


@needs_ortools
def test_red_lane_period(gd):
    p = dc.DirectChainParams(recipe=RED, target_rate=0.5, backend="cpsat")
    u = dc.generate(p, gd)
    assert u.meta["ratio"] == [1, 5] and u.meta["periods"] == 1
    assert dc.predict(u, gd)["throughput"] == pytest.approx(p.target_rate)
    # no port for the gear: its lane is left empty by the feeder (port group)
    assert "iron-gear-wheel" not in {i.material for i in u.interfaces}
    reqs = [r for g in u.groups for r in g.requires]
    assert any(getattr(r, "material", None) == "iron-gear-wheel" for r in reqs)
