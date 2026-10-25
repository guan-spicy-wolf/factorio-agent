"""Phase 4a: measured throughput of direct-insertion chains in headless Factorio.

Quantisation as in ``test_sim_measure``: the median may be one item per sample period
below the target. Recipes are unlocked by the characterizer (VERIFY §11)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fgen.gen.direct_chain import DirectChainParams, generate, predict
from fgen.sim.measure import RconCharacterizer

pytestmark = pytest.mark.headless

SAMPLE_S = 10.0


@pytest.fixture(scope="module")
def ch(server, gd):
    c = RconCharacterizer(server, gd)
    yield c
    c.clear_all()


@pytest.mark.parametrize("layout", ["solver", "baseline"])
@pytest.mark.parametrize("rate", [1.0, 2.0, 4.0])
def test_chain_meets_target(ch, gd, rate, layout):
    unit = generate(DirectChainParams(target_rate=rate, layout=layout), gd)
    m = ch.measure(unit, warmup_s=60, sample_s=SAMPLE_S, samples=6, speed=64)
    assert m["power"]["harness_on_unit_network"] and m["power"]["unpowered_inserters"] == 0
    assert m["power"]["inserter_networks"] == 1
    assert m["machines"] == predict(unit, gd)["machines"]
    assert "recipe_not_researched" not in m["machine_status_probes"]
    assert m["throughput"] >= rate - 1 / SAMPLE_S, m


def test_basic_inserter_chain(ch, gd):
    """24 basic inserters per period: the estimate (0.84/s) still carries 2/s."""
    unit = generate(DirectChainParams(target_rate=2.0, inserter="inserter"), gd)
    m = ch.measure(unit, warmup_s=60, sample_s=SAMPLE_S, samples=6, speed=64)
    assert m["throughput"] >= 2.0 - 1 / SAMPLE_S, m


def test_mixed_tier_chain(ch, gd):
    """The two-row period with long-handed inserters (over the iron belt onto the
    output belt) carries its target too."""
    p = DirectChainParams(target_rate=2.0, extra_inserters=("long-handed-inserter",))
    unit = generate(p, gd)
    assert (
        unit.meta["period"]["area"]
        < generate(replace(p, layout="baseline"), gd).meta["period"]["area"]
    )
    m = ch.measure(unit, warmup_s=60, sample_s=SAMPLE_S, samples=6, speed=64)
    assert m["throughput"] >= 2.0 - 1 / SAMPLE_S, m
