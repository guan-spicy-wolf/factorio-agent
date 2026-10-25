"""Phase 4a: the direct-chain generator family (solver and hand-written periods)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from fgen.blueprint import codec
from fgen.cli import main
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.gen.direct_chain import DirectChainParams, generate, plans, predict
from fgen.geom import belt as beltgeom
from fgen.geom.vec import Tile
from fgen.layout.model import PortSpace
from fgen.layout.validate import check_contract, validate_unit
from fgen.sim import harness as hs

RATES = (1.0, 2.0, 4.0)
RECIPE = "electronic-circuit"


def _product_rate(gd, machine="assembling-machine-1"):
    return tp.machine_rate(gd, RECIPE, machine)


@pytest.mark.parametrize("layout", ["solver", "baseline"])
@pytest.mark.parametrize("rate", RATES)
def test_units_pass_validate_and_meet_target(gd, rate, layout):
    p = DirectChainParams(palette="given", recipe=RECIPE, target_rate=rate, layout=layout)
    unit = generate(p, gd)
    assert validate_unit(unit, gd) == []
    pred = predict(unit, gd)
    assert pred["throughput"] >= rate - 1e-9
    a, b = unit.meta["ratio"]
    k = unit.meta["periods"]
    assert pred["machines"] == k * (a + b)
    assert k * b * _product_rate(gd) >= rate - 1e-9
    # fewest machines that meet the target among the period types
    best = plans(p, gd)[0]
    assert (best.spec.stages[1].count, best.spec.stages[0].count, best.periods) == (a, b, k)
    # every assembler has its recipe; the intermediate never reaches a port
    recipes = {
        q.extra.get("recipe")
        for q in unit.layout.placements
        if gd.entity(q.name).type == "assembling-machine"
    }
    assert recipes == {RECIPE, unit.meta["intermediate"]}
    assert unit.meta["intermediate"] not in {i.material for i in unit.interfaces}


def test_ratio_follows_target(gd):
    """Exact 3:2 when the target needs two consumers, approximate 2:1 for one."""
    r = _product_rate(gd)
    one = generate(DirectChainParams(palette="given", target_rate=r), gd)
    two = generate(DirectChainParams(palette="given", target_rate=2 * r), gd)
    assert one.meta["ratio"][1] == 1 and two.meta["ratio"][1] == 2
    assert two.meta["ratio"] == list(two.meta["exact_ratio"])
    assert one.meta["ratio"][0] / 1 > two.meta["exact_ratio"][0] / two.meta["exact_ratio"][1]
    out = next(i for i in one.interfaces if i.role == "output")
    assert any("approximate" in a for a in out.assumptions)


def test_contract_fields(gd):
    p = DirectChainParams(palette="given", target_rate=4.0)
    unit = generate(p, gd)
    assert check_contract(unit, gd) == []
    items = [i for i in unit.interfaces if i.kind == "item"]
    ins = [i for i in items if i.role == "input"]
    outs = [i for i in items if i.role == "output"]
    inter = unit.meta["intermediate"]
    cable_recipe = next(
        q.extra["recipe"]
        for q in unit.layout.placements
        if q.extra.get("recipe") not in (None, RECIPE)
    )
    raw = {n for n, _ in gd.recipe(RECIPE).ingredients} - {inter}
    raw |= {n for n, _ in gd.recipe(cable_recipe).ingredients}
    assert {i.material for i in ins} == raw
    assert [o.material for o in outs] == [RECIPE]
    for i in items:
        assert i.entity == p.belt and i.space is not None and i.port_group is not None
        rp = i.rate_profile
        assert rp.supply == "fixed" and math.isclose(sum(v for _, v in rp.lanes), i.rate)
        assert unit.group(i.port_group) is not None
    assert all(i.space == PortSpace.item("both") for i in ins)
    assert outs[0].space == PortSpace.item("direct")
    assert outs[0].rate >= p.target_rate
    # inputs sharing a belt: one group, lane materials on the group
    groups = {i.port_group for i in ins}
    for g in groups:
        mats = unit.group(g).lane_materials()
        members = [i for i in ins if i.port_group == g]
        if len(members) > 1:
            assert {mats[i.lane] for i in members} == {i.material for i in members}
    for i in ins:
        assert i.min_rate() is not None and i.min_rate() <= i.rate + 1e-9
    (power,) = [i for i in unit.interfaces if i.kind == "power"]
    assert power.entity == p.pole
    # input belts end inside the unit, at its east edge
    w, _ = unit.layout.size(gd)
    rows = {i.tile.y for i in ins}
    assert sorted(unit.meta["belt_terminals"]) == sorted(Tile(w - 1, y) for y in rows)


@pytest.mark.parametrize(
    "change",
    [
        {"pole": "medium-electric-pole"},
        {"inserter": "inserter"},
        {"machine": "assembling-machine-2", "target_rate": 3.0},
    ],
    ids=["medium-pole", "basic-inserter", "am2"],
)
@pytest.mark.parametrize("layout", ["solver", "baseline"])
def test_tiers_are_parameters(gd, change, layout):
    unit = generate(replace(DirectChainParams(palette="given", layout=layout), **change), gd)
    assert validate_unit(unit, gd) == []
    assert predict(unit, gd)["throughput"] >= unit.meta["target_rate"] - 1e-9


def test_solver_period_no_worse_than_baseline(gd):
    s = generate(DirectChainParams(palette="given", target_rate=2.0), gd).meta["period"]
    b = generate(DirectChainParams(palette="given", target_rate=2.0, layout="baseline"), gd).meta[
        "period"
    ]
    assert s["area"] <= b["area"]
    assert (s["area"], s["poles"] + s["inserters"]) <= (b["area"], b["poles"] + b["inserters"])
    assert s["seconds"] < 60 and not s["timed_out"]


def test_infeasible_beyond_one_belt(gd):
    full = beltgeom.items_per_second(gd, "transport-belt")
    with pytest.raises(Infeasible) as e:
        generate(DirectChainParams(palette="given", target_rate=full + 5), gd)
    assert "exceeds one transport-belt" in e.value.reason
    best = [c["target_rate"] for c in e.value.changes if "target_rate" in c]
    assert best and best[0] < full
    # the suggested target is feasible
    unit = generate(DirectChainParams(palette="given", target_rate=best[0]), gd)
    assert validate_unit(unit, gd) == []


def test_bad_names(gd):
    with pytest.raises(Infeasible):
        generate(DirectChainParams(palette="given", machine="stone-furnace"), gd)
    with pytest.raises(Infeasible):
        generate(
            DirectChainParams(palette="given", recipe="iron-plate", machine="assembling-machine-1"),
            gd,
        )
    with pytest.raises(Infeasible):
        generate(DirectChainParams(palette="given", inserter="burner-inserter"), gd)


def test_harness_wires_a_chain(gd):
    """Fixtures for every port route without violations (offline half of the measure)."""
    unit = generate(DirectChainParams(palette="given", target_rate=2.0), gd)
    h = hs.plan(unit, gd, Tile(0, 0))
    items = {item for _, item in h.sources}
    assert items == {i.material for i in unit.interfaces if i.role == "input" and i.kind == "item"}
    assert [item for _, item in h.sinks] == [RECIPE]


def test_cli_gen_direct_chain(capsys):
    assert main(["gen", "direct-chain", "--recipe", RECIPE, "--rate", "2"]) == 0
    out = capsys.readouterr().out
    assert "interfaces:" in out and "solver:" in out
    bp = codec.decode(out.strip().splitlines()[-1])
    ents = bp["blueprint"]["entities"]
    assert any(e.get("recipe") == RECIPE for e in ents)
    assert main(["gen", "direct-chain", "--rate", "100"]) == 2
    assert "try:" in capsys.readouterr().err
