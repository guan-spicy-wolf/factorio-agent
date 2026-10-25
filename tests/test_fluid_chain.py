"""Phase 5a: fluid machines on the direct_chain family (plastic, sulfuric acid). The
CP-SAT cases skip without OR-Tools; the exhaustive backend refuses fluids."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fgen.gen import direct_chain as dc
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import fluid
from fgen.layout.validate import validate, validate_unit
from fgen.solve import cpsat
from fgen.solve.period import CycleSpec, Stage, check_period, flows, tile

CP = "chemical-plant"
PLASTIC = dc.DirectChainParams(
    palette="given",
    recipe="plastic-bar",
    machine=CP,
    target_rate=2.0,
    backend="cpsat",
    extra_inserters=("long-handed-inserter",),
)
ACID = dc.DirectChainParams(
    palette="given", recipe="sulfuric-acid", machine=CP, target_rate=1.0, backend="cpsat"
)
needs_ortools = pytest.mark.skipif(not cpsat.available(), reason=cpsat.INSTALL_HINT)


def test_flows_split_fluids_from_items(gd):
    f = flows(CycleSpec((Stage("sulfuric-acid", CP, 1),)), gd)
    assert f.inputs == ("iron-plate", "sulfur") and f.outputs == ()
    assert f.fluid_inputs == ("water",) and f.fluid_outputs == ("sulfuric-acid",)
    rates = tp.ingredient_rates(gd, "sulfuric-acid", CP)
    assert f.input_rate["water"] == pytest.approx(rates["water"])
    assert f.output_rate["sulfuric-acid"] == pytest.approx(tp.machine_rate(gd, "sulfuric-acid", CP))


def test_one_stage_plans_cover_the_target(gd):
    rate = tp.machine_rate(gd, "plastic-bar", CP)
    for target in (rate / 2, rate, 2 * rate):
        pls = dc.plans(replace(PLASTIC, target_rate=target), gd)
        assert all(not pl.intermediates for pl in pls)
        assert all(pl.periods * pl.rate >= target - 1e-9 for pl in pls)


def test_exhaustive_backend_refuses_fluids(gd):
    with pytest.raises(Infeasible) as exc:
        dc.generate(replace(PLASTIC, backend="exhaustive"), gd)
    assert "backend='cpsat'" in exc.value.suggestions
    assert "CP-SAT" in exc.value.reason


@needs_ortools
@pytest.mark.parametrize("target", [1.0, 2.0, 4.0])
def test_plastic_units_validate_and_carry_the_fluid_contract(gd, target):
    unit = dc.generate(replace(PLASTIC, target_rate=target), gd)
    assert validate_unit(unit, gd) == []
    gas = [i for i in unit.interfaces if i.kind == "fluid"]
    assert [(i.material, i.role) for i in gas] == [("petroleum-gas", "input")]
    (g,) = gas
    need = tp.ingredient_rates(gd, "plastic-bar", CP)["petroleum-gas"]
    machines = dc.predict(unit, gd)["machines"]
    assert g.rate == pytest.approx(need * machines)
    assert g.min_rate() == pytest.approx(need * target / tp.machine_rate(gd, "plastic-bar", CP))
    assert g.temperature == gd.fluids["petroleum-gas"].default_temperature
    assert 0 < (g.segment_extent or 0) < gd.constant("pipeline_extent")
    assert dc.predict(unit, gd)["throughput"] >= target
    # the plants' recipe gas boxes all face gas: validate checks it per placement
    plants = [p for p in unit.layout.placements if p.name == CP]
    assert len(plants) == machines


@needs_ortools
def test_parameters_only_change_the_variant(gd):
    """Mirror, pole and inserter tiers are parameters (plan-phase5a §3)."""
    base = dc.generate(PLASTIC, gd)
    mirrored = dc.generate(replace(PLASTIC, mirror=True), gd)
    plants = [p for p in mirrored.layout.placements if p.name == CP]
    assert plants and all(p.extra.get("mirror") is True for p in plants)
    assert all("mirror" not in p.extra for p in base.layout.placements if p.name == CP)
    # the two plastic ports are symmetric: mirroring keeps the same period
    assert mirrored.meta["period"]["width"] == base.meta["period"]["width"]
    assert mirrored.meta["period"]["height"] == base.meta["period"]["height"]
    for change in (
        {"pole": "medium-electric-pole"},
        {"inserter": "inserter", "extra_inserters": ()},
    ):
        u = dc.generate(replace(PLASTIC, **change), gd)
        assert validate_unit(u, gd) == []
        names = {p.name for p in u.layout.placements}
        assert {v for v in change.values() if isinstance(v, str)} <= names


@needs_ortools
def test_plastic_period_is_provably_smallest(gd):
    spec = CycleSpec((Stage("plastic-bar", CP, 1),), extra_inserters=("long-handed-inserter",))
    res = cpsat.solve(spec, gd, time_limit=60.0)
    per = res.best
    assert per is not None and per.meta["proved_optimal"] and not res.stats.timed_out
    assert check_period(per, gd) == []
    # area lower bound by hand: the plant, a pipe tile beside its input side, two belt
    # rows and the inserter row between plant and belts, three columns wide
    w, h = gd.entity(CP).tile_width or 3, gd.entity(CP).tile_height or 3
    assert per.area >= w * (h + 1 + 1 + 2)
    lay = tile(per, 4)
    fl = {k * len(per.placements) + i: f for k in range(4) for i, f in enumerate(per.fluid_of) if f}
    codes = {v.code for v in validate(lay, gd, fluids=fl)}
    assert not {c for c in codes if c.startswith(("fluid", "power", "overlap", "inserter"))}


@needs_ortools
@pytest.mark.parametrize("target", [1.0])
def test_sulfuric_acid_outputs_on_a_pipe(gd, target):
    unit = dc.generate(replace(ACID, target_rate=target), gd)
    assert validate_unit(unit, gd) == []
    out = [i for i in unit.interfaces if i.role == "output"]
    assert [(i.kind, i.material) for i in out] == [("fluid", "sulfuric-acid")]
    (o,) = out
    machines = dc.predict(unit, gd)["machines"]
    assert o.rate == pytest.approx(tp.machine_rate(gd, "sulfuric-acid", CP) * machines)
    assert o.rate >= target
    assert o.temperature == gd.fluids["sulfuric-acid"].default_temperature
    # water in on its own pipe, no idle or foreign fluid port touches either network
    assert {(i.material, i.role) for i in unit.interfaces if i.kind == "fluid"} == {
        ("water", "input"),
        ("sulfuric-acid", "output"),
    }
    for p in unit.layout.placements:
        if p.name == CP:
            pts = fluid.machine_ports(gd, CP, p.position, p.direction, p.extra["recipe"])
            assert {pt.fluid for pt in pts} == {"water", "sulfuric-acid"}
