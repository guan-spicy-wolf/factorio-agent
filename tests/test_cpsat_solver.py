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
    p = dc.DirectChainParams(target_rate=1.0, backend="cpsat")
    unit = dc.generate(p, gd)
    assert unit.meta["period"]["backend"] == "cpsat"
    ex = dc.generate(replace(p, backend="exhaustive"), gd)
    assert unit.meta["period"]["area"] == ex.meta["period"]["area"]
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
        dc.generate(dc.DirectChainParams(backend="cpsat"), gd)
    assert "backend='exhaustive'" in exc.value.suggestions
