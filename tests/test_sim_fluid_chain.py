"""Phase 5a: fluid machines measured in headless Factorio (plastic: gas from an
infinity-pipe, coal on a belt; sulfuric acid: water in, acid counted at a tank whose
segment the harness empties every tick). Needs OR-Tools for the periods."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fgen.data.runtime import RuntimeState
from fgen.gen import direct_chain as dc
from fgen.sim.measure import RconCharacterizer
from fgen.solve import cpsat

pytestmark = [
    pytest.mark.headless,
    pytest.mark.skipif(not cpsat.available(), reason=cpsat.INSTALL_HINT),
]

CP = "chemical-plant"
PLASTIC = dc.DirectChainParams(
    palette="given",
    recipe="plastic-bar",
    machine=CP,
    target_rate=1.0,
    backend="cpsat",
    extra_inserters=("long-handed-inserter",),
)


@pytest.fixture(scope="module")
def ch(server, gd):
    c = RconCharacterizer(server, gd)
    yield c
    c.clear_all()


def _check(m, unit, gd):
    assert m["power"]["harness_on_unit_network"] and m["power"]["unpowered_inserters"] == 0
    assert "recipe_not_researched" not in m["machine_status_probes"]
    pred = dc.predict(unit, gd, RuntimeState.from_json(m["runtime"]))
    assert m["throughput_mean"] == pytest.approx(pred["throughput"], rel=0.02), m
    return pred


def test_plastic_meets_target(ch, gd):
    unit = dc.generate(PLASTIC, gd)
    m = ch.measure(unit, warmup_s=60, sample_s=10, samples=6, speed=64)
    _check(m, unit, gd)
    assert m["throughput_mean"] >= PLASTIC.target_rate
    # full research: the plastic productivity bonus is in the recorded state
    assert RuntimeState.from_json(m["runtime"]).productivity("plastic-bar") > 0


def test_sulfuric_acid_output_matches_contract(ch, gd):
    unit = dc.generate(replace(PLASTIC, recipe="sulfuric-acid", extra_inserters=()), gd)
    m = ch.measure(unit, warmup_s=60, sample_s=10, samples=6, speed=64)
    _check(m, unit, gd)
    (out,) = [i for i in unit.interfaces if i.role == "output"]
    assert out.kind == "fluid"
    assert m["throughput_mean"] == pytest.approx(out.rate, rel=0.02)
