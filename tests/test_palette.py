"""Phase 4c: entity costs from the recipes, presets, palettes, the anytime driver."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from fractions import Fraction

import pytest

from fgen.data import cost
from fgen.gen import direct_chain as dc
from fgen.gen import direct_chain_sweep as dcs
from fgen.gen import fluid_sweep as fs
from fgen.geom import footprint
from fgen.solve import cpsat
from fgen.solve.period import (
    CycleSpec,
    SkeletonBelt,
    Stage,
    assemble,
    check_period,
    flows,
)
from fgen.solve.preset import PRESETS, metrics, scale_for

AM1 = "assembling-machine-1"
needs_ortools = pytest.mark.skipif(not cpsat.available(), reason=cpsat.INSTALL_HINT)


def _by_recipe(gd, item: str) -> Fraction:
    """The cost of ``item`` recomputed from its one recipe in the data."""
    (r,) = [r for r in gd.recipes.values() if r.results and r.results[0][0] == item]
    total = sum(Fraction(a).limit_denominator() * cost.raw_cost(gd, n) for n, a in r.ingredients)
    return total / Fraction(r.results[0][1]).limit_denominator()


def test_costs_derive_from_the_recipes(gd):
    # raw materials (no recipe makes them) are one unit each
    for raw in ("iron-ore", "copper-ore", "wood", "petroleum-gas"):
        assert not any(raw == res[0] for r in gd.recipes.values() for res in r.results)
        assert cost.raw_cost(gd, raw) == cost.RAW
    for item in (
        "iron-plate",
        "copper-cable",
        "electronic-circuit",
        "iron-gear-wheel",
        "inserter",
        "fast-inserter",
        "long-handed-inserter",
        "transport-belt",
        "underground-belt",
        "small-electric-pole",
        "substation",
        "pipe-to-ground",
    ):
        assert cost.raw_cost(gd, item) == _by_recipe(gd, item), item
    # entities cost their item; one without a recipe in the data is refused
    assert cost.entity_cost(gd, "fast-inserter") == cost.raw_cost(gd, "fast-inserter")
    with pytest.raises(ValueError):
        cost.entity_cost(gd, "burner-inserter")


def test_preset_keys_are_lexicographic(gd):
    sc = scale_for(gd, ["fast-inserter", "small-electric-pole", "transport-belt"], 32, 16)
    a = {"area": 100, "poles": 9, "inserters": 30, "cost": 10, "kinds": 3}
    b = {**a, "area": 101, "poles": 1, "inserters": 1}
    assert sc.key("compact", a) < sc.key("compact", b)
    c = {**a, "area": 400, "cost": 9}
    assert sc.key("cheap", c) < sc.key("cheap", a) and sc.key("compact", a) < sc.key("compact", c)
    d = {**a, "area": 400, "kinds": 2}
    assert sc.key("simple", d) < sc.key("simple", a)
    assert set(PRESETS) == {"compact", "cheap", "simple"}
    # integer costs are exact multiples of 1/scale
    assert all(Fraction(v, sc.scale) == cost.entity_cost(gd, n) for n, v in sc.cost.items())


def test_full_palette_comes_from_the_data(gd):
    p = dc.DirectChainParams()
    pal = dc.palette(p, gd)
    usable = {
        n
        for n, e in gd.entities.items()
        if e.type == "inserter" and e.is_electric and footprint.size(gd, n) == (1, 1)
    }
    assert set(pal["inserters"]) == usable and pal["inserters"][0] == p.inserter
    assert set(pal["poles"]) == {n for n, e in gd.entities.items() if e.type == "electric-pole"}
    assert set(pal["belts"]) == {n for n, e in gd.entities.items() if e.type == "transport-belt"}
    speeds = {gd.entity(b).speed for b in pal["belts"]}
    assert {gd.entity(u).speed for u in pal["undergrounds"]} == speeds
    narrow = dc.palette(replace(p, exclude=tuple(pal["undergrounds"])), gd)
    assert narrow["undergrounds"] == ()
    given = dc.palette(replace(p, palette="given"), gd)
    assert given == {
        "inserters": (p.inserter,),
        "poles": (p.pole,),
        "belts": (p.belt,),
        "undergrounds": (),
    }
    # the exhaustive search and the baseline only take the given tiers, and prove
    ex = dc.resolve_backend(replace(p, backend="exhaustive"))
    assert ex.palette == "given" and ex.prove


def test_dominated_entries_leave_the_palette(gd):
    spec = CycleSpec(
        (Stage("electronic-circuit", AM1, 2), Stage("copper-cable", AM1, 3)),
        extra_inserters=("bulk-inserter", "long-handed-inserter"),
        extra_poles=("medium-electric-pole", "substation"),
    )
    out, gone = cpsat.undominated(spec, gd)
    # no research: the bulk inserter has the fast one's reach and capacity, and costs more
    assert gone == ["bulk-inserter"]
    assert out.extra_inserters == ("long-handed-inserter",)
    assert out.poles == spec.poles  # no pole tier dominates another


def test_underground_pairs_in_a_belt_row(gd):
    spec = CycleSpec((Stage("electronic-circuit", AM1, 1),), undergrounds=("underground-belt",))
    f = flows(spec, gd)
    belts = [SkeletonBelt("output", ("electronic-circuit",))]
    reach = gd.entity("underground-belt").max_distance

    def period(ugs):
        return assemble(spec, gd, 10, 4, belts, [0], [(0, 0, 1)], [], [], f, belt_undergrounds=ugs)

    ok = period({0: {2: "input", 3: "gap", 4: "gap", 5: "output"}})
    rows = [p for p in ok.placements if p.position.y < 1]
    assert sum(1 for p in rows if p.name == "underground-belt") == 2
    assert not any(p.position.x in (3.5, 4.5) for p in rows)  # the free tiles
    assert not [m for m in check_period(ok, gd) if "belt row" in m]
    # an entrance with no exit, and a pair longer than the reach, break the row
    for bad in (
        {0: {2: "input", 3: "gap"}},
        {0: {1: "input", **{x: "gap" for x in range(2, 2 + reach)}, 2 + reach: "output"}},
    ):
        assert [m for m in check_period(period(bad), gd) if "belt row" in m]
    assert ok.belts[0].belt == "transport-belt"


@needs_ortools
def test_anytime_matches_the_proof_on_a_small_spec(gd):
    spec = CycleSpec((Stage("electronic-circuit", AM1, 1), Stage("copper-cable", AM1, 2)))
    a = cpsat.solve(spec, gd, time_limit=60)
    b = cpsat.solve(spec, gd, time_limit=120, prove=True)
    assert a.best is not None and b.best is not None
    assert a.stats.proven and b.stats.proven
    assert (a.best.area, a.best.meta["poles"], a.best.meta["inserters"]) == (
        b.best.area,
        b.best.meta["poles"],
        b.best.meta["inserters"],
    )
    ph = a.stats.phases
    assert ph["first_feasible_s"] <= ph["area_s"] <= ph["objective_s"] <= ph["total_s"]
    assert check_period(a.best, gd) == []
    sc = scale_for(gd, ["fast-inserter", "small-electric-pole", "transport-belt"], 32, 16)
    assert a.best.meta["metrics"] == metrics(a.best, gd, sc)
    # deterministic slices: the same spec gives the same period
    again = cpsat.solve(spec, gd, time_limit=60)
    assert again.best is not None and again.best.placements == a.best.placements


@needs_ortools
def test_anytime_infeasible_is_proven_or_labelled(gd):
    """R = 6 on a yellow belt (three 3:2 periods): no period; within a short box the
    anytime driver either proves it or says it did not."""
    spec = CycleSpec((Stage("electronic-circuit", AM1, 2), Stage("copper-cable", AM1, 3)))
    res = cpsat.solve(spec, gd, repeats=3, time_limit=5)
    assert res.best is None
    assert res.stats.proven != res.stats.unproven_infeasible


def test_sweep_rows_keep_their_measurements(gd):
    """The phase-4c fields at their legacy values leave the sweep keys unchanged, so
    regenerating keeps the stored measurements of unchanged blueprints."""
    for mod, path in (
        (dcs, "characterization/direct_chain.jsonl"),
        (fs, "characterization/fluid_chain.jsonl"),
    ):
        with open(path) as fh:
            stored = {dcs._key(json.loads(line)["params"]) for line in fh if line.strip()}
        for c in mod.cases():
            assert dcs._key(asdict(c.params)) in stored, c.name
            assert c.params.palette == "given" and c.params.prove, c.name


def test_gen_does_not_simulate_by_default(monkeypatch, capsys):
    from fgen import cli
    from fgen.sim import server

    def boom(*a, **k):
        raise AssertionError("a server was started")

    monkeypatch.setattr(server.HeadlessServer, "__init__", boom)
    assert cli.main(["gen", "smelting", "--rate", "7.5"]) == 0
    args = cli.build_parser().parse_args(["gen", "direct-chain"])
    assert args.simulate is False and args.preset == "compact" and not args.prove
    assert args.palette == "full"
