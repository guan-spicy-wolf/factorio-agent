"""Phase 4b task A: the CP-SAT period backend against the exhaustive search.

Skipped (not failed) when OR-Tools is not installed; the "not installed" path itself
is tested by hiding the module."""

from __future__ import annotations

import builtins
from dataclasses import replace

import pytest

from fgen.gen import direct_chain as dc
from fgen.gen.base import Infeasible
from fgen.geom.vec import Tile
from fgen.layout.validate import validate
from fgen.solve import cpsat, cycle
from fgen.solve.period import CycleSpec, Period, Stage, check_period, flows, tile

AM1 = "assembling-machine-1"
needs_ortools = pytest.mark.skipif(not cpsat.available(), reason=cpsat.INSTALL_HINT)


def spec(a: int = 3, b: int = 2, **kw) -> CycleSpec:
    return CycleSpec((Stage("electronic-circuit", AM1, b), Stage("copper-cable", AM1, a)), **kw)


def tiled_violations(per: Period, n: int, gd) -> list:
    ends = [Tile(n * per.width - 1, b.y) for b in per.belts]
    return validate(tile(per, n), gd, belt_terminals=ends)


@needs_ortools
@pytest.mark.parametrize("ab", [(2, 1), (3, 2)], ids=["2:1", "3:2"])
def test_cpsat_matches_exhaustive(gd, ab):
    """Same optimal area; poles no more than the exhaustive search (it minimises poles
    per machine placement, CP-SAT over the whole period); both pass the judges."""
    sp = spec(*ab)
    a = cycle.solve(sp, gd, time_limit=60)
    b = cpsat.solve(sp, gd, time_limit=120)
    assert a.best is not None and b.best is not None
    assert not b.stats.timed_out
    assert b.best.area == a.best.area
    assert b.best.meta["poles"] <= a.best.meta["poles"]
    assert check_period(b.best, gd) == []
    for n in (4, 8):
        assert tiled_violations(b.best, n, gd) == [], n
    # the intermediate stays off the belts, as in the exhaustive period
    assert {m for r in b.best.belts for m in r.materials()} == {
        "copper-plate",
        "iron-plate",
        "electronic-circuit",
    }


@needs_ortools
def test_cpsat_proves_lane_capacity_infeasible(gd):
    """R = 6 on a yellow belt: three 3:2 periods overload the shared copper lane and a
    copper belt of its own cannot reach the circuit machines: no period (proved)."""
    f = flows(spec(), gd)
    from fgen.gen import throughput as tp

    lane = tp.belt_lane_capacity(gd, "transport-belt")
    k = int(lane // f.input_rate["copper-plate"]) + 1
    res = cpsat.solve(spec(), gd, repeats=k, time_limit=120)
    assert res.best is None and not res.stats.timed_out
    assert res.stats.pruned["lane_capacity"] > 0


@needs_ortools
def test_generator_backend_switch(gd):
    p = dc.DirectChainParams(palette="given", target_rate=1.0, backend="cpsat")
    unit = dc.generate(p, gd)
    assert unit.meta["period"]["backend"] == "cpsat"
    ex = dc.generate(replace(p, backend="exhaustive"), gd)
    assert unit.meta["period"]["area"] == ex.meta["period"]["area"]
    assert ex.meta["params"]["palette"] == "given" and ex.meta["params"]["prove"]
    assert dc.predict(unit, gd)["throughput"] >= p.target_rate


def test_unavailable_backend_is_explained(gd, monkeypatch):
    real = builtins.__import__

    def no_ortools(name, *a, **kw):
        if name.startswith("ortools"):
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_ortools)
    assert not cpsat.available()
    with pytest.raises(cpsat.SolverUnavailable, match="--extra solver"):
        cpsat.solve(spec(2, 1), gd)
    with pytest.raises(Infeasible) as exc:
        dc.generate(dc.DirectChainParams(palette="given", backend="cpsat"), gd)
    assert "backend='exhaustive'" in exc.value.suggestions


def test_auto_backend_follows_the_install(gd, monkeypatch):
    p = dc.DirectChainParams(palette="given")
    assert p.backend == "auto"
    want = "cpsat" if cpsat.available() else "exhaustive"
    assert dc.resolve_backend(p).backend == want
    monkeypatch.setattr(cpsat, "available", lambda: False)
    assert dc.resolve_backend(p).backend == "exhaustive"


@needs_ortools
def test_cpsat_is_deterministic(gd):
    """The same spec gives the same period (blueprint shas keep measurements)."""
    a = cpsat.solve(spec(2, 1), gd, time_limit=60).best
    b = cpsat.solve(spec(2, 1), gd, time_limit=60).best
    assert a is not None and b is not None and a.placements == b.placements


@needs_ortools
def test_proof_driver_is_the_same_in_parallel(gd):
    """Phase 7: the proof driver solves independent models in forked processes; the
    process count changes the clock only (same statuses, same period, same stats)."""
    sp = spec(2, 1)
    old = cpsat.PROCS
    try:
        cpsat.PROCS = 1
        a = cpsat.solve(sp, gd, time_limit=120, prove=True)
        cpsat.PROCS = 4
        b = cpsat.solve(sp, gd, time_limit=120, prove=True)
    finally:
        cpsat.PROCS = old
    assert a.best is not None and b.best is not None
    assert a.stats.proven and b.stats.proven
    assert sorted(b.best.layout().placements, key=repr) == sorted(
        a.best.layout().placements, key=repr
    )
    assert a.stats.pruned["cp_screen_infeasible"] == b.stats.pruned["cp_screen_infeasible"]
    assert a.stats.pruned["cp_first_infeasible"] == b.stats.pruned["cp_first_infeasible"]


def _skeletons(ctx):
    from fgen.solve.period import belt_sets, input_lanes_ok

    sets = belt_sets(ctx.flows, ctx.lanes, ctx.spec.lane_items)
    return [b for b in sets if input_lanes_ok(b, ctx.flows, ctx.lane_cap, ctx.lanes, 1)]


def _red_direct() -> CycleSpec:
    return CycleSpec((Stage("automation-science-pack", AM1, 2), Stage("iron-gear-wheel", AM1, 1)))


@needs_ortools
@pytest.mark.parametrize("which", ["red-direct", "3:2"])
def test_slim_models_are_exact(gd, which):
    """Plan-phase8 C: single-machine stages without anchor-indexed flows (``aggregate``)
    and rows no belt assignment allows left out (``prune``) are the same model: the same
    status and the same optimum at every size (a size that fits and one that does not)."""
    from fgen.solve import preset as presets
    from fgen.solve.cycle import SolveStats

    sp = _red_direct() if which == "red-direct" else spec()
    ctx = cpsat._Ctx(sp, gd, 1)
    ctx.scale = presets.scale_for(gd, ctx.palette_names(), 32, 16)
    best = cpsat.solve(sp, gd, time_limit=120, prove=True).best
    assert best is not None
    for belts in _skeletons(ctx):
        for w in (best.width - 1, best.width):
            out = set()
            for agg, prune in ((False, False), (True, False), (True, True)):
                st, per, obj = cpsat._solve_height(
                    ctx, best.height, w, w, belts, 120, 4, SolveStats(), aggregate=agg, prune=prune
                )
                assert st in ("OPTIMAL", "INFEASIBLE"), st
                if per is not None:
                    assert check_period(per, gd) == []
                out.add((st, obj))
            assert len(out) == 1, (belts, w, out)


@needs_ortools
def test_static_rows_hold_the_solved_periods(gd):
    """Every belt row and machine anchor row of a solved period is one the static row
    analysis allows; a height too small for the machines and belts is refuted."""
    for sp in (_red_direct(), spec(2, 1), spec()):
        per = cpsat.solve(sp, gd, time_limit=120, prove=True).best
        assert per is not None
        ctx = cpsat._Ctx(sp, gd, 1)
        rows = {(b.role, tuple(sorted(b.materials()))): b.y for b in per.belts}
        sk = next(
            b
            for b in _skeletons(ctx)
            if sorted((s.role, tuple(sorted(s.materials))) for s in b) == sorted(rows)
        )
        allowed = ctx.belt_rows(per.height, sk, per.width)
        assert allowed is not None
        ok_rows, anchors = allowed
        for k, s in enumerate(sk):
            assert (k, rows[(s.role, tuple(sorted(s.materials)))]) in ok_rows
        for q, si in zip(per.placements, per.stage_of, strict=True):
            if si is None:
                continue
            _, mh = ctx.msize[si]
            assert round(q.position.y - mh / 2) in anchors[si], (q, si)
        mh = max(h for _, h in ctx.msize)
        assert all(ctx.belt_rows(mh, b, 32) is None for b in _skeletons(ctx))
        # one band of machines (these periods): side by side they need their total width
        total = sum(w * s.count for (w, _), s in zip(ctx.msize, sp.stages, strict=True))
        assert per.height < 2 * mh + len(sk) and total <= per.width
        assert ctx.belt_rows(per.height, sk, total - 1) is None
